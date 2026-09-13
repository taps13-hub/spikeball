# Spikeball

A Python/Django leaderboard for a small spikeball group: public rankings,
2v2 result entry, player rating history, invite-only accounts, and audited
administrator corrections.

Start with [SPEC.md](SPEC.md) for the design decisions and component explanations.

## How the app works

Django receives browser requests and renders HTML templates. PostgreSQL stores
accounts, players, results, and rating history. The browser only needs a little
JavaScript for the chart; there is no React, Node build, or separate API.
Chart.js 4.5.1 is vendored under `static/js/` with its MIT license. Its unused
source-map reference is removed so static-file collection needs no map download.

Every player starts at **1000 ELO**. A team's rating is the average of its two
players. The update uses **K = 32**, without score-margin weighting. Four equal
players become 1016/1016 and 984/984 after their first match.

Results are the source of truth. Adding, correcting, or voiding a result
replays active matches in `(played_at, id)` order, within one PostgreSQL
transaction. A shared row lock serializes writers. This intentionally favors
correctness and simplicity over high write throughput.

## Run locally

Requirements: Python 3.12 or newer compatible with Django 5.2, PostgreSQL 16 or
newer, and PostgreSQL command-line tools. Use PostgreSQL for development too:
SQLite does not provide the locking guarantees used here.

### 1. Install the Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The virtual environment isolates this project's Python packages from other
projects. Activate it again in each terminal. If your system provides `uv`,
`uv venv .venv` and `uv pip install -r requirements.txt` are equivalent options.

### 2. Create a local database

If PostgreSQL already accepts your operating-system username over a local Unix
socket:

```bash
createdb spikeball
```

On a fresh Ubuntu PostgreSQL installation, an administrator can first create
a development role matching your OS username:

```bash
sudo -u postgres createuser --createdb "$USER"
createdb spikeball
```

Do not recreate an existing role/database or give the application a production
superuser account. Local tests need permission to create a temporary database.
If your database uses a password or runs on another host, configure
`DATABASE_URL` as described below instead.

### 3. Configure your environment

```bash
cp .env.example .env
# Edit .env for your database and group timezone.
set -a
source .env
set +a
```

`.env` is a local shell configuration file and is ignored by Git. Django reads
environment variables; it does not load this file automatically. Use only a
trusted file when sourcing shell configuration. Reload it in each new terminal.

By default, development uses the database `spikeball`, your OS username, and a
local Unix socket. `DJANGO_DEBUG=1` enables local development behavior and prints
email messages in your terminal instead of sending them.

Set `TIME_ZONE` to an IANA name such as `Australia/Perth` or `Europe/London`.
Data is stored in UTC, and the form explicitly labels your configured timezone.

### 4. Create tables and the initial administrator

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Migrations create the application/authentication tables, the rating lock row,
and the shared rate-limit cache table. The superuser command prompts for your
username, email, and password; no default account or password is shipped.

Open **http://localhost:8000/**. Manage the group at **/admin/**.

## First session with friends

1. Log in as your administrator and add player profiles through **Add player**.
   Profiles are separate from accounts: players do not need to log in to be
   included in results.
2. In **Admin > Invitations > Add invitation**, enter a friend's email.
   In local development, copy the invitation URL from the terminal. Treat it
   like a password and do not share terminal logs.
3. Your friend opens the link and chooses a username and password. Invitations
   expire after seven days by default and work once. Their email comes from the
   invitation and cannot be changed during signup.
4. Any invited account can add players and record results. Select four distinct
   players, enter non-negative integer scores with no tie, and set the match time.
5. View the public leaderboard and click a player for their chart and recent games.

The scoring form does not enforce a winning score or win-by-two. A submission
token prevents retries/double-clicks from recording one form twice, but genuine
rematches with the same players and scores are allowed.

## Correcting mistakes

In **Admin > Matches**, open a result and choose **Correct result**. Edit its
players, scores, or played-at time and give a reason. Alternatively, choose
**Void this result** to retain the record but remove its effect on ratings.

All later ratings are rebuilt. Voided matches are excluded from public result
lists, win/loss records, and rating history. The original and corrected values
are preserved in the staff-only **Match revisions** list. Voids are not
reversible through the UI in v1; enter a new correct result if needed.

Current ratings and rating-history rows are read-only in admin. Hard match
deletion is disabled. Disable accounts instead of deleting their attribution.
Normal friends cannot use administrator correction or invitation tools.

Invitations have **Resend** and **Revoke** actions. Resending creates a new link
and invalidates the old one. A delivery failure leaves the invitation saved,
with an explicit error and the ability to resend.

## Validation during development

```bash
python manage.py test
python manage.py makemigrations --check --dry-run
```

The test runner creates and removes a separate `test_spikeball` PostgreSQL
database; never point tests at a production database server. Tests cover the
pure ELO formula, replay and corrections, rollback/concurrency, permissions,
invitations/password resets, forms, charts, and cross-worker rate limiting.

After changing models, use `python manage.py makemigrations` and
`python manage.py migrate`, then commit the migration with the code.

## Deploying

Choose a managed Python host, a managed PostgreSQL database, and an SMTP email
provider. A provider may supply more than one of these. No hosting account,
paid resource, public deployment, or external email service is created by this
repository.

Check current pricing, sleeping/expiry policies, database durability, connection
limits, SMTP availability, and backup/restore features before selecting plans.
A free web plan does not imply a permanent free database or working SMTP.

### Production environment

| Variable | Purpose |
| --- | --- |
| `DJANGO_DEBUG=0` | Disable development mode. This is the default when unset. |
| `DJANGO_SECRET_KEY` | A strong generated secret; production will not start without it. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated app hostnames, without URL schemes. |
| `PUBLIC_BASE_URL` | Canonical HTTPS origin for invitations/reset links, e.g. `https://spikeball.example.com`. |
| `DATABASE_URL` | Provider PostgreSQL URL, normally including `?sslmode=require`. Overrides local `PG*` connection settings. |
| `TIME_ZONE` | Your group's display timezone. |
| `EMAIL_HOST`, `EMAIL_PORT` | SMTP server and port; port defaults to 587. |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP credentials. |
| `EMAIL_USE_TLS=1` | Use STARTTLS; confirm your provider supports it. |
| `DEFAULT_FROM_EMAIL` | A provider-verified sender address. |
| `INVITATION_DAYS` | Invitation validity, default 7. Password reset tokens last one hour. |
| `TRUST_PROXY_HTTPS=1` | Trust the ingress's `X-Forwarded-Proto` only when it strips client-supplied values and direct backend access is blocked. |
| `FORWARDED_ALLOW_IPS` | Trusted proxy addresses for Gunicorn; set according to the host's documentation. |
| `PORT`, `WEB_CONCURRENCY` | Gunicorn port (8000) and worker count (2). |

Generate the Django secret locally with
`python -c 'import secrets; print(secrets.token_urlsafe(64))'` and save it in
your host's secret settings. Never commit it.

Use these provider-independent commands:

```bash
# Build
python -m pip install -r requirements.txt
python manage.py collectstatic --noinput

# Release: run once, not independently in every web worker
python manage.py migrate --noinput
python manage.py check --deploy

# Web process: reads gunicorn.conf.py, including PORT
gunicorn config.wsgi:application
```

Set production environment variables before all these commands. Create the
initial superuser through the host's secure console. HTTPS is required;
production enables secure cookies, HTTPS redirects, and HSTS.
The deployment check may warn about HSTS subdomains/preloading: these are
deliberately not enabled before you control the final domain and can guarantee
HTTPS for every subdomain. Do not enable them just to silence the warnings.

Gunicorn access logs are disabled to keep reset/invitation tokens out of request
logs. Django's request logging redacts those URL paths. Configure the hosting
ingress/CDN to redact or omit these paths too. Never enable local console-email
mode on a shared production service.

### Rate limiting

`django-ratelimit` limits login to 10 POSTs/minute, password-reset requests to
5/hour, and invitation acceptance to 20/hour, per source IP. Admin login is
also limited. PostgreSQL-backed cache counters are protected by a transaction
advisory lock so limits work across Gunicorn workers without Redis. This small
adapter has a concurrent increment test; Django's unmodified database cache
does not provide atomic increments.

The app uses `REMOTE_ADDR`, not untrusted forwarded headers. Configure the host
to supply the real client address through a trusted ingress, or apply equivalent
limits there. If only a proxy address reaches Django, users behind that proxy
share a limit; do not "fix" this by blindly trusting `X-Forwarded-For`.

### Email and launch checks

Verify sender/domain ownership with your SMTP provider, then send an invitation
and complete a password reset using the public URL. Reset requests deliberately
give a generic response; SMTP failures in that flow are logged for operators
without revealing whether an account exists.

Before inviting the group, confirm mobile forms work, app restarts preserve
results, static assets load over HTTPS, and a database backup can be restored.
These external deployment steps require your chosen provider and credentials.

## Backups and recovery

Enable automated database backups and choose a retention period with your
provider. Keep backups private: they include emails and password hashes.
Git commits and Django migrations are not database backups.

For a manual PostgreSQL backup, with a securely configured connection:

```bash
pg_dump --format=custom --file=spikeball.dump "$DATABASE_URL"
```

Restore into a **new, empty recovery database**, never over the live database:

```bash
createdb spikeball_recovery
pg_restore --no-owner --dbname=spikeball_recovery spikeball.dump
```

On a managed host, create the recovery database through the provider and pass
its connection URL instead. Inspect the restored data, run the app against the
recovery database, and verify account/result/history consistency before a
planned switchover. Do not run tests against the recovery or production database.
Document the host-specific restore steps and periodically rehearse them.

## Intentionally not included

No public signup, 1v1 games, seasons, approval voting, mobile app, or background
queue. Full replay and globally serialized writes are deliberate small-group
trade-offs. Optimize only if real usage makes them too slow.
