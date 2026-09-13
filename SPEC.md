# Spikeball ELO leaderboard - build spec

## Goal and scope

A responsive web app where a small group of friends record 2v2 spikeball
results and each player's ELO rating updates automatically.

The leaderboard and player history are public. Only invited, logged-in friends
can add players and results. Administrators issue invitations and correct or
void results. Support backdated matches and recalculate ratings chronologically.

This spec replaces the original Next.js/TypeScript/Supabase design with a
Python-first application.

## Stack and component responsibilities

Use a supported Django LTS release with a compatible supported Python version.
Pin dependencies when scaffolding the project.

| Component | What it does | Why it is here |
| --- | --- | --- |
| Python + Django | Runs the application: URLs, views, forms, authentication, database access, and admin tools. A view is a Python function that receives a request and returns a response. | Keeps most work in familiar Python and avoids assembling separate frameworks. |
| Django templates + HTML/CSS | Render pages on the server, with placeholders and loops filled by Python views. CSS makes them responsive. | No React, TypeScript, separate frontend, or frontend build pipeline required. |
| PostgreSQL | Stores players, matches, ratings, accounts, and invitations. Supports transactions and concurrent-write locking. | Reliable rating updates even when friends submit results simultaneously. |
| Django ORM + migrations | Models and queries written in Python translate into SQL. Migrations version database schema changes. | Keep application/database changes together without writing SQL for ordinary queries. |
| Django Auth | Handles password hashing, sessions, login, password changes, and password resets. | Reuse established authentication rather than implementing it ourselves. |
| Django admin | Provides a staff-only management interface. | Manage invitations, accounts, players, and corrections without building another frontend. |
| Email provider | Delivers invitations and password-reset messages through Django's SMTP backend. | Django creates messages; an external mail service delivers them. |
| Chart.js | Draws the rating-history chart in the browser. | A small amount of JavaScript without a React dependency. Serve a pinned local distribution. |
| Gunicorn | Runs Django as a production web process. | Django's development server is only for local development. |
| WhiteNoise | Serves collected CSS and JavaScript through the app. | Avoid a separate static-file service at this scale. |
| Managed Python host | Runs the app with HTTPS and environment configuration. | Reduces server administration; provider selection remains open. |
| GitHub | Stores code and can trigger deployment. | Version control, not storage or backup for live match data. |

### How the pieces fit

```text
Browser --HTTPS--> Gunicorn/Django --> PostgreSQL
                        |
                        +--> Email provider
                        |
Browser <--HTML/CSS-----+
        + JavaScript for the chart
```

The browser never connects directly to PostgreSQL. Django checks permissions,
validates input, queries the database, and renders a response. Database and
email credentials stay on the server.

### Key design decisions

- **One Django app, not a separate frontend/API:** simpler to learn, deploy,
  and debug. Normal HTML forms are sufficient.
- **PostgreSQL instead of SQLite:** SQLite could handle the traffic, but its
  database file needs persistent single-server storage and its writes do not
  support PostgreSQL-style row locks. PostgreSQL was selected for reliable
  concurrency and flexible managed hosting, not because traffic is high.
- **Django Auth instead of Supabase Auth:** one account system integrated with
  the application and admin. No Supabase SDK, browser API, or RLS policy layer
  is needed; enforce permissions on the server and invariants in the database.
- **Python ELO inside a Django transaction:** a Postgres stored function cannot
  directly call the original spec's TypeScript function. This architecture
  keeps one authoritative formula in Python without duplicating it in SQL.
- **Full chronological replay:** a backdated result can affect every later
  rating. Rebuilding all ratings is easier to reason about than separate
  incremental and correction paths, and suits a small group.

## Application layout

```text
manage.py
requirements.txt
.env.example
config/                 # Django settings, root URLs, WSGI entry point
leaderboard/
    models.py           # Database models and constraints
    forms.py            # Input validation and form presentation
    views.py            # HTTP request handling
    urls.py
    elo.py              # Pure Python calculation, no database access
    services.py         # Transactional writes, replay, invitation acceptance
    admin.py
    migrations/
    tests/
templates/
    base.html
    leaderboard/
    registration/
static/
    css/
    js/
```

Start with one Django application. Do not add generic repositories, plugin
systems, background workers, or a separate API.

## Data model

### Accounts are not players

A Django account identifies someone who can log in. A Player identifies
someone whose games are tracked. One account can enter results for four people
who have no accounts. No account-to-player link is required in v1.

Use username/password login, with email for invitations and password resets.
Public pages never expose account emails.
Use a minimal Django AbstractUser subclass to enforce normalized, unique email
addresses at the database level, including concurrent account creation.

### Models

Use ordinary Django primary keys. Player references are foreign keys, meaning
the database ensures each referenced player exists.

| Model | Fields and rules |
| --- | --- |
| Player | `id`, `name`, `rating` (default 1000), `created_at`. Names are display labels, not unique identifiers; disambiguate duplicate names in selection forms. |
| Match | `id`, `team1_player1`, `team1_player2`, `team2_player1`, `team2_player2`, `team1_score`, `team2_score`, `played_at`, `recorded_at`, `created_by`, unique `submission_token`, nullable `voided_at` and `voided_by`. |
| RatingHistory | `id`, `player`, `match`, `rating` immediately after that match. Unique on `(player, match)`; four rows per active match after replay. |
| RatingState | One internal row seeded by migration, used as the shared write lock. Not editable in admin. |
| Invitation | `id`, normalized email, hashed random token, expiry, inviter, nullable `accepted_at` and `revoked_at`. Never persist the raw token. |
| MatchRevision | `id`, match, actor, action, timestamp, reason, and before/after match values. Immutable, staff-only audit record for corrections and voids. |

### Database and validation rules

- Require four distinct players. Enforce all pairwise distinctions with
  database constraints as well as form validation.
- Scores must be non-negative integers with no ties. Do not enforce a target
  score or win-by-two rule.
- Store ratings as Decimal values with six decimal places. Round for display,
  not to whole numbers during calculation.
- Protect referenced players from deletion. Do not expose direct rating/history
  edits or hard match deletion. Disable accounts rather than losing attribution.
- Store timezone-aware timestamps in UTC. Configure and label the group's
  display timezone in forms and pages.
- `played_at` means when the match happened; `recorded_at` means when it was
  submitted. Replay and chart ordering use `(played_at, id)`.
- Derive win/loss records from non-voided matches rather than storing separate
  mutable counters. New players show rating 1000 and a 0-0 record.

## ELO logic

Implement `leaderboard/elo.py::calculate_elo_update` as a pure Python function:
four player ratings and two scores in, four updated ratings out. It must not
depend on Django or perform database operations.

Team-averaged ELO, starting rating 1000, K = 32:

1. `team1_avg = (team1_player1_rating + team1_player2_rating) / 2`
2. `team2_avg = (team2_player1_rating + team2_player2_rating) / 2`
3. `expected1 = 1 / (1 + 10 ** ((team2_avg - team1_avg) / 400))`
4. `actual1 = 1 if team1_score > team2_score else 0`
5. `delta = 32 * (actual1 - expected1)`
6. Quantize the shared delta to six decimal places, then add it to both team 1
   ratings and subtract it from both team 2 ratings.

Using the same delta with opposite signs preserves total rating across all
four players. Reject draws before calculation. Score margin does not affect
the result.

Example: four players start at 1000. The winners each become 1016, and the
losers each become 984.

## Transactional writes, replay, and corrections

A transaction commits all its changes together or rolls them all back. A row
lock makes another writer wait, preventing simultaneous rating calculations
from overwriting each other.

Every result submission, admin correction, and void uses one shared service:

1. Check permissions and validate the request on the server.
2. Begin `transaction.atomic()` and lock the existing RatingState row with
   `select_for_update()` before changing results or reading replay inputs.
3. Save the match mutation and any required MatchRevision.
4. Load players and all non-voided matches in `(played_at, id)` order.
5. Start an in-memory rating map with every player at 1000.
6. Replay matches through the pure ELO function, collecting four history
   entries per match.
7. Replace derived history and batch-update current player ratings.
8. Commit together. On failure, retain the previous state and surface an error.

All normal and admin writes must follow this path; default admin saves, bulk
actions, or deletes must not bypass it. Coordinate player creation through the
same lock so replay uses a consistent player set.

Administrators may correct players, scores, or played-at time, or void a
match, with a reason and recorded before/after values. Invited friends may
submit but cannot correct or void results.

Voided matches remain stored and auditable but are excluded from rankings,
win/loss records, rating history, and normal public result lists. Corrections
and backdated entries can legitimately change previously displayed ratings.
Match ID is a deterministic tie-breaker for identical timestamps.

Use a per-form submission UUID with a database uniqueness guarantee to prevent
double-clicks or retries from saving the same submission twice. Do not deduplicate
by player/score combinations: genuine rematches are allowed. Redirect after a
successful POST.

## Pages and permissions

| Route | Access | Behavior |
| --- | --- | --- |
| `/` | Public | Leaderboard: name, rating, wins, losses, player link. Sort by stored rating descending with a stable tie order. |
| `/players/<id>/` | Public | Current rating, chronological history/chart, recent active matches. Support players with no games. |
| `/players/new/` | Invited account | Validated player creation. |
| `/matches/new/` | Invited account | Pick two players per team, enter scores and played-at time, submit through the shared service. |
| `/accounts/login/` | Public | Username/password login; no public signup. |
| `/accounts/logout/` | Logged-in account | CSRF-protected POST logout. |
| `/accounts/password_change/` | Logged-in account | Built-in password change. |
| `/accounts/password_reset/` | Public | Built-in reset request and token-based confirmation flow. Do not reveal whether an account exists. |
| `/invitations/accept/<token>/` | Valid invitation | Choose username/password; take email from the invitation, not editable browser input. |
| `/admin/` | Administrator | Manage accounts/players, issue/revoke/resend invitations, correct or void results, inspect revisions. |

Use Django forms and messages for actionable errors. Check authorization on
every write, not just by hiding buttons. Use ORM aggregation/prefetching to
avoid one query per leaderboard row.

Use Django's safe JSON embedding helpers for chart data. Keep history readable
without JavaScript, and make forms usable on mobile and with a keyboard.

## Invitations, authentication, and email

- Administrators issue expiring, single-use invitations; acceptance creates a
  non-staff user. No public account-creation endpoint.
- Generate invitation tokens with Python's `secrets` module and store only
  hashes. Reject expired, revoked, and previously accepted tokens.
- Lock the invitation and atomically create the account/mark it accepted.
  Concurrent acceptance must not create duplicate accounts.
- Prevent duplicate accounts for the same normalized email, including
  concurrent invitation acceptance; keep admin account creation consistent
  with this rule and password-reset behavior.
- Use Django's password validators, hashing, sessions, CSRF middleware,
  template autoescaping, and built-in password-reset tokens.
- Use the console email backend locally and configured SMTP in production.
  Generate email links from a trusted configured public base URL.
- Commit an invitation before sending mail. Email delivery is outside the
  database transaction: failures must be visible to the administrator and
  recoverable through resend/revoke, never reported as successful delivery.
- Resending an invitation rotates its token and invalidates the old link;
  raw tokens cannot be recovered from their stored hashes.
- Throttle login, reset requests, and invitation acceptance through host
  facilities or a maintained Django integration. The implementation uses
  `django-ratelimit` with PostgreSQL-backed, transaction-locked cache counters
  shared across workers. Limits are 10 login POSTs/minute, 5 reset requests/hour,
  and 20 invitation acceptance POSTs/hour per source IP. Only trust client IP
  information supplied by a correctly configured ingress.
- Keep account emails, invitation records, and correction audit details private.
  Public player names should be names friends are comfortable publishing.

## Hosting, configuration, and operations

Use PostgreSQL locally and in production so development exercises the same
transaction and locking behavior. Document local installation; a container is
optional, not required.

Production needs Python app hosting, managed PostgreSQL, and outbound email.
Render is one possible app/database host, not a committed choice. Prefer
low-cost services appropriate for extremely small traffic.

Before choosing providers, confirm current prices, sleep behavior, database
expiry, backup/restore features, connection limits, and SMTP/network support.
Do not assume permanent free hosting. Email providers may require a verified
sender/domain even when the app uses a free provider subdomain.

Deployment and README instructions must explain:

1. Creating a virtual environment, installing dependencies, configuring
   PostgreSQL, applying migrations, and starting local Django.
2. Configuring database credentials, `SECRET_KEY`, email credentials, public
   base URL, allowed hosts, and display timezone through environment variables.
3. Installing dependencies, collecting static files, and applying migrations
   once in a release step rather than in each web worker.
4. Running Gunicorn with provider-managed HTTPS and WhiteNoise static files.
5. Creating the initial superuser and sending the first invitation.
6. Configuring automated database backups, retention, and a restore procedure.

Production must disable `DEBUG`, use secure session/CSRF cookies, and configure
trusted proxies correctly. Never commit secrets or log passwords or raw tokens;
redact invitation/reset tokens from request logs as well.
Commit only placeholder configuration examples.

Database migrations change the schema; they are not backups. GitHub preserves
code, not users or match results. Database data must survive app restarts and
redeployments.

## Suggested build order

1. Scaffold Django, dependencies, configuration, templates, and local setup docs.
2. Add models, constraints, migrations, singleton write lock, and protected admin.
3. Implement and unit-test the pure ELO calculation.
4. Implement transactional submissions, chronological replay, audited corrections,
   voiding, and retry-safe submission handling.
5. Implement invite-only authentication, invitation delivery/acceptance,
   password reset, throttling, and explicit email error handling.
6. Build authenticated player creation and match-entry forms.
7. Build the public leaderboard and player pages with history charts.
8. Complete deployment configuration, operating documentation, and acceptance
   checks; select hosting/email providers before going live.

## Acceptance criteria

Use Django's built-in test runner and PostgreSQL-backed `TransactionTestCase`
for concurrency/transaction cases; no additional test framework is required.

- Equal 1000-rated teams produce 1016/1016 and 984/984 after one result.
- Favorites, upsets, and player/team permutations follow the formula, preserve
  total rating, and reject invalid input.
- Each active match has exactly four history rows; current ratings agree with
  a fresh replay.
- Backdated matches, score/player/date corrections, voids, and timestamp ties
  produce deterministic results.
- Forced failures roll back results, ratings, history, and audit changes.
- Concurrent submissions preserve both games; repeated submission tokens save
  only one game.
- Forms and database constraints reject repeated players, negative scores,
  non-integer scores, and draws.
- Anonymous users cannot write; friends cannot invite, correct/void results,
  edit ratings directly, or read private audit/account information.
- Invitations and reset links reject invalid, expired, or reused tokens;
  concurrent acceptance and duplicate-email cases cannot create extra accounts.
- Email failures are reported appropriately and invitations remain recoverable.
- Win/loss counts and charts exclude voided results and reflect corrections.
- Empty player histories, mobile layouts, keyboard access, and text alternatives
  are usable.
- Production supports HTTPS, static files, email, persistent database storage,
  migrations, and a documented, exercised backup/restore procedure.

Run focused tests during implementation, then the full small suite and Django
deployment checks before release.

## Non-goals for v1

- No 1v1 matches, margin-of-victory weighting, seasons, or multiple leagues.
- No public signup, social login, or match approval voting.
- No native mobile app or notifications beyond invitations/password resets.
- No separate REST API, React frontend, Redis, Celery, WebSockets, Kubernetes,
  or custom password/session system.
- No incremental ELO optimization until measured replay performance warrants it.

Hosting provider, email provider, and group display timezone remain deployment
choices. They do not block implementing and running the application locally.
