# First deployment: Railway, public viewing, no email

This guide launches the existing app with one owner account. Anyone can view the
rankings and player histories. You log in to add player profiles and record games
for everyone. Your friends do not need accounts.

There is no Resend setup, email-sending domain, or public registration. Email
invitations and password resets stay disabled. Your existing local database is
not uploaded, cleaned, or changed.

## What the pieces mean

| Term | What it does here |
| --- | --- |
| Railway project | A group containing your website service and database service. |
| Web service | Runs Django, the Python application that serves your pages. |
| PostgreSQL service | Stores accounts, players, matches, ratings, and sessions across app restarts. |
| Domain | Your website address. Railway provides one with HTTPS; you need not buy one. |
| Environment variable | A deployment setting entered in Railway, such as a database connection or secret key. It is not something to paste into Git. |
| Docker image | A packaged copy of the app and its dependencies that Railway builds using the repository's `Dockerfile`. |
| Migration | A command that creates or updates database tables without replacing the database. |

Railway hosting, database resources, and backups can cost money. Review the
[current plans](https://docs.railway.com/pricing/plans), usage limits, and billing
alerts before creating services. Railway's PostgreSQL template is not a promise
of fully managed database maintenance; you are responsible for backups and upgrades.

## 1. Prepare the source

The source needs the Dockerfile, `.dockerignore`, and **all** model migrations.
Commit the intended app changes to your GitHub repository and push the branch
you will deploy. Do not commit `.env`, database dumps, or passwords.

Use GitHub's **Actions** tab to check the release workflow. It runs tests with
PostgreSQL and builds/smoke-tests the image without production secrets. Configure
required checks/your deployment process so a failing workflow is not promoted;
a workflow file alone does not enforce Railway's deployment policy.

The app's static assets are collected inside the image. Do not put `collectstatic`
in Railway's pre-deploy command: that command runs in a separate container and
its filesystem changes do not carry into the web container.

## 2. Create the project and database

1. Sign in to [Railway](https://railway.com) and create a project.
2. Choose **Deploy from GitHub repo**, connect your GitHub account if prompted,
   and select this repository and the intended branch. The web service uses the
   root `Dockerfile` automatically.
3. Add a **PostgreSQL** service to the same project/environment. Use PostgreSQL 16
   or newer and retain its persistent volume.
4. Keep the database private. The web service can reach it through Railway's
   private network without opening a public database port.

The first automatic web deployment may fail before settings are supplied; inspect
the error and finish the next steps before redeploying. Never "fix" startup by
turning on production debug mode.

## 3. Generate the website address

Open the **web service**, then **Settings > Networking > Public Networking >
Generate Domain**. Railway supplies an address similar to
`your-service.up.railway.app` and handles its HTTPS certificate.

Keep the generated hostname for the next step. Do not add a TCP proxy/public
backend port to the web service; only the Railway HTTPS ingress should expose it.

## 4. Configure private deployment settings

Open the web service's **Variables** section. Enter values individually, or use
its raw editor. Replace example values below with your own. Do not paste a local
`.env` wholesale: it enables debug and points at your local database.

| Variable | Value for this launch |
| --- | --- |
| `DJANGO_DEBUG` | `0` |
| `DJANGO_SECRET_KEY` | A newly generated random secret (see below). |
| `DJANGO_ALLOWED_HOSTS` | `your-service.up.railway.app,healthcheck.railway.app` (hostnames only, no spaces or `https://`). |
| `PUBLIC_BASE_URL` | `https://your-service.up.railway.app` (no additional path). |
| `DATABASE_URL` | A reference to the PostgreSQL service's private `DATABASE_URL`. |
| `EMAIL_FEATURES_ENABLED` | `0` |
| `TIME_ZONE` | Your IANA timezone, for example `Australia/Perth`. |
| `TRUST_PROXY_HTTPS` | `1` for the Railway HTTPS ingress, subject to the verification below. |
| `TRUST_PROXY_CLIENT_IP` | Initially `0`; verify the edge behavior below before setting `1`. |
| `WEB_CONCURRENCY` | `2` initially; review memory use after deployment. |

Railway supplies `PORT`; Gunicorn listens on it. No email variables or credentials
are needed. Keep Gunicorn's default `FORWARDED_ALLOW_IPS`; do not set it to `*`
as a substitute for understanding your proxy.

Use Railway's variable-reference picker to select your database service's
`DATABASE_URL`. If the database service is named `Postgres`, the reference is:

```text
${{Postgres.DATABASE_URL}}
```

The service name must match yours. Reference the private URL, not a local Unix
socket or the external `DATABASE_PUBLIC_URL`. Retain the provider-supported
connection/TLS parameters. Other providers may require `sslmode=require`; do not
remove a provider's TLS requirement or append conflicting options blindly.

Generate the secret **locally**, not in a Git-tracked file:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Paste the result into Railway's private `DJANGO_SECRET_KEY` variable. Store it
securely, do not share terminal screenshots containing it, and keep it stable
across redeployments so existing sessions remain valid.

## 5. Configure release, startup, and readiness

In the web service's deployment settings, configure:

| Setting | Value |
| --- | --- |
| Builder | Dockerfile (detected automatically). |
| Pre-deploy command | `python manage.py check --deploy && python manage.py migrate --noinput` |
| Start command | Leave unset to use the image's `gunicorn config.wsgi:application` command. |
| Healthcheck path | `/healthz/` |
| Healthcheck timeout | `300` seconds. |
| Pre-deploy timeout | `300` seconds initially; review if migrations grow. |
| Restart policy | On failure, with a bounded retry count such as 5. |

Apply the staged changes and deploy. Check build, pre-deploy, and runtime logs
separately. A failed migration must stop the deployment, not be ignored.

`check --deploy` may warn about HSTS subdomains/preloading. Those options are
intentionally off until you control the relevant domain scope; do not enable
them just to remove warnings.

Readiness returns `ok` only when the app can query PostgreSQL. A database failure
returns 503 without exposing connection details. Railway probes using the
`healthcheck.railway.app` hostname; that is why it appears in allowed hosts.
Only the exact health path permits HTTP inside the hosting network.

Railway healthchecks protect deployment activation; they are **not continuous
uptime monitoring**. A separate uptime monitor can be chosen later.

## 6. Verify the proxy before trusting visitor IPs

Railway documents `X-Forwarded-Proto: https` and `X-Real-IP` at its
[public ingress](https://docs.railway.com/networking/public-networking/specs-and-limits).
The app must trust only headers written by that ingress, not arbitrary visitors.

- Keep the web backend inaccessible through public TCP ports or other untrusted
  routes. Other services in its private network must also be trusted.
- Confirm the hostname uses HTTPS without a redirect loop.
- With `TRUST_PROXY_CLIENT_IP=0`, login is limited by the direct connection IP.
  Behind a proxy, visitors can share a bucket. Do not disable rate limiting.
- Confirm with the provider that client-supplied `X-Real-IP` and
  `X-Forwarded-Proto` are overwritten. Then set `TRUST_PROXY_CLIENT_IP=1`,
  redeploy, and perform the following probe (replace the hostname):

```bash
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  -H 'X-Real-IP: deliberately-invalid' \
  -H 'X-Forwarded-Proto: http' \
  https://your-service.up.railway.app/accounts/login/
```

Expect **200**, not 400 or a redirect. A 400 can mean an untrusted header reached
the app unchanged; a redirect can mean the scheme was not normalized. Do not
continue with trusted-header settings until the ingress boundary is understood.
The probe supplements provider confirmation; it does not prove every bypass
route is closed.

Once enabled, the app validates a single IPv4/IPv6 `X-Real-IP` and uses it for
existing limits, including admin login. Missing/invalid values return 400; an
internal `/healthz/` probe may omit this header. `X-Forwarded-For` is ignored.
Verify that two independent clients, such as home internet and mobile data, do
not share login attempt limits. If the provider does not guarantee this boundary,
leave trust off and resolve per-client limiting before considering launch complete.

## 7. Create your administrator account

Create it **inside the deployed web service**, not against your local database.
Install the [Railway CLI](https://docs.railway.com/guides/cli) using its official
instructions on your computer, then, from this repository:

```bash
railway login
railway link
railway ssh
```

During linking/connection, select the correct project, production environment,
and **web service**, not the database service. The CLI may ask to register an SSH
key. Do not share the private key or authentication tokens.

Inside the connected container:

```bash
python manage.py createsuperuser
```

Choose a username and strong password. Django also asks for an email address;
this command does not send email, and email features remain disabled.

Exit the remote shell with `exit`. Open `https://your-service.up.railway.app/admin/`
and log in. Never configure automatic superuser creation on each startup.
`railway run` runs commands on your own computer with Railway variables; it is
not equivalent to a shell inside the service and may not reach a private database.

## 8. Add players and record games

Use **Add player** for each participant, then **Record match** to select four
players and enter the result. Player profiles are not accounts. You do not need
to invite anyone or create more login accounts.

Open the website in a private/incognito browser window to confirm visitors can
view rankings and player history without logging in. Only authorized accounts
can add results or use admin corrections.

With `EMAIL_FEATURES_ENABLED=0`, invitations disappear from admin and direct
invitation/password-reset requests return an explicit 403. A previous invitation
link cannot create an account, and a previous reset link cannot change a password
while the features are disabled. This is intentional, not an email outage.

If already logged in, use **Change password**. If you forget the password, reconnect
with `railway ssh` and run, replacing `your_username`:

```bash
python manage.py changepassword your_username
```

This prompts for a new password without email. Keep your Railway account and SSH
access secure: they can control the application and its database.

## 9. Backups, updates, and recovery

In PostgreSQL's **Backups** tab, enable a schedule and review retention and cost.
Take a backup before substantial schema changes. A Git commit or web deployment
is not a database backup. Deleting a database volume can also delete its backups.

Rehearse recovery into a **separate empty database** before relying on the app.
Use the [README's PostgreSQL dump/restore procedure](../README.md#backups-and-recovery)
with securely configured connections. A local machine cannot resolve Railway's
private database hostname; use an approved SSH tunnel or run database tools in
the database environment. Do not open a public database port just for convenience.
Keep dumps private and use a PostgreSQL client compatible with the server version.
The web image intentionally does not include PostgreSQL backup command-line tools.

Railway's volume restore replaces the attached volume on the selected service.
It is **not** a harmless restore rehearsal on production. Read the
[backup documentation](https://docs.railway.com/volumes/backups) and use a separate
recovery target for testing.

For updates, review and push code, confirm CI passes, and deploy the intended
revision. The release job migrates the existing database; it does not reset data.
Use backwards-compatible migrations when old and new versions overlap. A failed
healthcheck can prevent promotion, but migrations may already have changed the
database. Rolling back the image does not roll back those changes.

After redeploying, verify previous players/results are still present. Investigate
missing data by checking the database reference, not by reseeding or deleting it.

## Launch checklist

- Anonymous visitors can view standings, profiles, and histories.
- Owner login, player creation, result entry, rating updates, and corrections work.
- Anonymous writes fail; invitation and password-reset routes are disabled.
- CSS, images, and charts load over HTTPS, and forms pass CSRF checks.
- Verified proxy headers preserve per-visitor rate limits.
- Healthchecks pass; restarts and redeployments preserve records.
- A database backup has been restored and checked in a separate recovery database.
- Billing alerts and backup retention are understood.

Until external deployment and recovery checks are done, the repository is prepared
for deployment, not evidence of a verified live production service.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing secret / invalid public URL at startup | Set a real secret, debug off, and an HTTPS origin in the web service's Variables; redeploy. |
| PostgreSQL connection failure | Verify the private service reference, credentials/TLS, environment, and database health. Do not substitute SQLite. |
| Healthcheck 400 | Include `healthcheck.railway.app` in allowed hosts; inspect any trusted-IP validation error. |
| Healthcheck redirects | Use exactly `/healthz/`, with its trailing slash. Do not turn off HTTPS site-wide. |
| Healthcheck 503 | PostgreSQL is unavailable; inspect database/release logs. |
| Redirect loop / CSRF 403 | Verify canonical HTTPS hostname and edge scheme handling; do not disable CSRF or allow wildcard origins. |
| Static-file 404/500 | Rebuild the image and inspect `collectstatic` output; do not rely on files generated in the release job. |
| Login rate limit affects other visitors | Verify edge-supplied IPs before enabling `TRUST_PROXY_CLIENT_IP`; never accept arbitrary forwarded headers. |
| Invitations or password-reset URLs return 403 | Expected for this launch. Recover the owner password through the secure console. |
| Forgot owner password | Run `python manage.py changepassword your_username` inside the deployed web service. |
| Data disappears after a deploy | Confirm the app points to the same persistent PostgreSQL service/environment. Stop before changing or deleting volumes. |

Leave email disabled until it is explicitly requested later. Enabling it requires
a functioning production email service and verified invitation/reset flows; merely
flipping the flag does not configure delivery.
