#!/usr/bin/env bash
set -euo pipefail

# Only the containers and network created here are removed; no local database is used.
image=${1:-spikeball:ci}
network=
database=
web=
cleanup() {
    status=$?
    if (( status != 0 )); then
        [[ -z "$web" ]] || docker logs "$web" >&2 || true
        [[ -z "$database" ]] || docker logs "$database" >&2 || true
    fi
    [[ -z "$web" ]] || docker rm --force "$web" >/dev/null || true
    [[ -z "$database" ]] || docker rm --force --volumes "$database" >/dev/null || true
    [[ -z "$network" ]] || docker network rm "$network" >/dev/null || true
    exit "$status"
}
trap cleanup EXIT

docker image inspect "$image" >/dev/null
if docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" \
    | grep -Eq '^(DJANGO_DEBUG|DJANGO_SECRET_KEY)='; then
    echo "Build-only Django settings leaked into the image environment." >&2
    exit 1
fi
if docker run --rm "$image" python manage.py check; then
    echo "The production image unexpectedly started without runtime credentials." >&2
    exit 1
fi

created=$(docker network create "spikeball-smoke-$$-${GITHUB_RUN_ID:-local}")
network=$created
created=$(docker create --network "$network" --network-alias database \
    --env POSTGRES_DB=spikeball_smoke --env POSTGRES_USER=spikeball_smoke \
    --env POSTGRES_PASSWORD=smoke-only-password postgres:16)
database=$created
docker start "$database" >/dev/null
for attempt in {1..60}; do
    if docker exec "$database" pg_isready -h 127.0.0.1 -U spikeball_smoke -d spikeball_smoke >/dev/null; then
        break
    fi
    sleep 1
done
docker exec "$database" pg_isready -h 127.0.0.1 -U spikeball_smoke -d spikeball_smoke

app_env=(
    --network "$network"
    --env DATABASE_URL=postgresql://spikeball_smoke:smoke-only-password@database:5432/spikeball_smoke
    --env DJANGO_SECRET_KEY=smoke-only-secret-not-for-production-0123456789abcdefghijklmnopqrstuvwxyz
    --env DJANGO_DEBUG=0
    --env DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
    --env PUBLIC_BASE_URL=https://localhost
    --env EMAIL_FEATURES_ENABLED=0
    --env TRUST_PROXY_HTTPS=1
    --env PORT=8080
    --env WEB_CONCURRENCY=1
)
docker run --rm "${app_env[@]}" "$image" python manage.py check --deploy
docker run --rm "${app_env[@]}" "$image" python manage.py migrate --noinput
created=$(docker create "${app_env[@]}" "$image")
web=$created
docker start "$web" >/dev/null
docker exec "$web" python -c 'import os; assert os.getuid() != 0, "Container runs as root"'

docker exec --interactive "$web" python - <<'PY'
import http.client
import re
import time


def request(path, *, secure=True, method="GET"):
    connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=5)
    headers = {"X-Forwarded-Proto": "https"} if secure else {}
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


for attempt in range(60):
    try:
        if request("/healthz/", secure=False)[0] == 200:
            break
    except OSError:
        pass
    time.sleep(1)
else:
    raise AssertionError("Gunicorn did not become ready on PORT=8080")

assert request("/", secure=False)[0] == 301, "Ordinary HTTP must redirect to HTTPS"
assert request("/healthz/extra", secure=False)[0] == 301, "Health exemption must be exact"
assert request("/healthz/", secure=False, method="HEAD")[0] == 200
status, headers, body = request("/")
assert status == 200, (status, body)
assert "password_reset" not in body.decode(), "Disabled email link remains visible"
login_status, _, login_body = request("/accounts/login/")
assert login_status == 200
assert "password_reset" not in login_body.decode(), "Disabled reset link remains on login"
for path in (
    "/accounts/password_reset/",
    "/accounts/reset/smoke-user/smoke-token/",
    "/invitations/accept/smoke-token/",
):
    status, _, denied_body = request(path)
    assert status == 403 and b"Email features are disabled" in denied_body, (path, status)
assert request("/players/new/")[0] == 302
assert request("/matches/new/", method="POST")[0] == 403
assert request("/admin/")[0] == 302
css_paths = re.findall(rb'["\'](/static/[^"\']+\.css)["\']', body)
assert css_paths, "No production CSS found in rendered HTML"
for path in css_paths:
    assert re.search(rb"\.[0-9a-f]{12}\.css$", path), path
    status, headers, content = request(path.decode())
    assert status == 200 and content and headers["Content-Type"].startswith("text/css")
    assert "immutable" in headers.get("Cache-Control", ""), headers

# Resolve the actual manifest names, then fetch them through Gunicorn/WhiteNoise.
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.contrib.staticfiles.storage import staticfiles_storage

for asset in ("js/player-chart.js", "js/chart.umd.min.js"):
    path = staticfiles_storage.url(asset)
    assert re.search(r"\.[0-9a-f]{12}\.js$", path), path
    status, headers, content = request(path)
    assert status == 200 and content and "javascript" in headers["Content-Type"]
print("Production HTTP, no-email routes, permissions, PORT and manifest assets passed.")
PY

docker stop --time 5 "$database" >/dev/null
docker exec --interactive "$web" python - <<'PY'
import http.client

connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=10)
connection.request("GET", "/healthz/")
response = connection.getresponse()
assert response.status == 503, (response.status, response.read())
print("Database outage correctly returns readiness 503.")
PY

docker stop --time 10 "$web" >/dev/null
[[ "$(docker inspect --format '{{.State.ExitCode}}' "$web")" == 0 ]]
echo "Production container smoke checks passed, including graceful shutdown."
