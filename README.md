# Campus Lost & Found Platform

**EduRev P09 — Campus Lost and Found Platform with Ownership Verification and Custody Chain (Track P: Python + Django + PostgreSQL).**

One place to report lost and found items on campus. Finders record identifying details that are **never shown publicly**; campus security checks every claim against those hidden details before anything is released, and every handover is supervised and recorded in an append-only custody chain.

## Problem statement

Lost property on campus is handled through notice boards and WhatsApp groups: there is no matching between lost and found reports, no check that a claimant really owns an item, and no record of what was handed to whom.

## Objectives

* Capture lost and found reports with photos, category, location and time.
* Match found items to lost reports on description, attributes, location and time proximity.
* Hold identifying details privately (encrypted) for ownership verification.
* Require ownership verification before any handover.
* Record a supervised handover with a custody chain and proof.
* Age unclaimed items into a disposal workflow after a configured period.

## Features

| Module | What is implemented |
|---|---|
| M1 Item reporting | Separate lost/found flows, mobile-first form, camera capture, photo upload (Pillow resize + EXIF/GPS stripping), category attributes, validation |
| M2 Taxonomy | Two-level categories with public attributes and retention overrides; campus location tree (area › building › floor › room), custody points, searchable selector, optional Leaflet map picker |
| M3 Matching | Weighted, explainable score from category, text (PostgreSQL `pg_trgm`), attributes, location tree/distance and time; suggestions on create/update and a nightly refresh command |
| M4 Private details & verification | Separate `PrivateDetail` model with its own `vault` manager, Fernet-encrypted, no reverse accessor. Ownership is verified by security against these hidden details (every view of them is audited); the scored question challenge (exact/keyword/fuzzy, attempt + rate limits, no hint about which answer failed) remains available |
| M5 Claims | Short claim form (description, where, when, extra info, optional private photo), statuses Under review / More information requested / Approved / Awaiting collection / Returned / Rejected, staff information requests and claimant replies, never auto-approved, duplicate prevention, competing claims resolved explicitly, one approved claim per item (row locking + partial unique constraint) |
| M6 Custody & handover | Receive/transfer/correct (append-only, PostgreSQL trigger), scheduling with email + in-app notice, three-step handover page: confirm claimant → verify a collection code emailed to the claimant's registered address (hashed, 10-minute, attempt-limited, single-use) or a drawn signature → Complete Handover with ID check and optional proof photo; item becomes Returned only then, in one transaction with the custody release and the finder's reward |
| M7 Ageing & disposal | Configurable retention, warnings, disposal-review queue, escalation, two-person disposal (security review + authorised decision), protection for claimed/returned items |
| M8 Public board | Redacted board built from a public DTO, PostgreSQL full-text + `pg_trgm` search, filters, sorting, infinite scroll (numbered pages without JavaScript), live filtering, cached with Django's cache framework (invalidated on any item change), JSON endpoint `/api/board/` |
| Report review | Every new report is reviewed by the Lost & Found team (verify / reject with a reason, duplicate hints). Optional setting holds reports off the board until verified; receiving an item into custody verifies it |
| Community rewards | Append-only points ledger with unique event references (no double awards), finder points for a verified found report and a return bonus paid only after a completed handover, configurable policy, badges, opt-out leaderboard, staff-authorised corrections |

Plus dashboards for students/staff, the Lost & Found team and administrators, analytics from live data (return rate, median time to return, hotspots, category mix, disposals, verification failure rate), in-app and email notifications, an audit log, a separate two-step staff sign-in (`/staff/login/`) and full account management (create, edit, roles, block, delete, send reset link).

## Roles

| Role (Django group) | Can do |
|---|---|
| Students & Staff | Browse, report lost/found items, claim, answer verification, see own reports/claims/notifications |
| Security Personnel (shown as **Lost & Found team**) | Verify or reject new reports, team dashboard, intake/custody/transfers, add private details, review verification evidence, approve/reject claims, schedule and complete handovers, disposal review |
| Campus Administrator | Categories, locations, retention and report-review policy, reward policy, badges and points corrections, authorise disposal, analytics, audit log |
| System Administrator | Create and manage every account (students, staff, team, administrators), roles, matching weights/thresholds, verification and OTP limits, audit log |

Roles are additive. Permissions are enforced in views (decorators) **and** again in each service function.

## Architecture

```
config/            settings (env-driven), URLs, error handlers
lostfound/
  models/          one file per area; PrivateDetail lives alone in privacy.py
  services/        business rules: matching, verification, claims, custody, disposal, public, analytics, images, crypto
  views/           public, accounts, member, security, administration, errors
  templates/       base.html + partials/ (sidebar, topbar, site header/footer, messages,
                   item_card, status_badge, pagination, field, icons) + one folder per area
  static/          app.css (design system), app.js (progressive enhancement only)
  management/commands/  refresh_matches, check_ageing, check_email, purge_demo_data,
                   import_sqlite_export, rotate_encryption_keys
  migrations/      0001 schema, 0002 PostgreSQL extension/indexes/triggers, 0003–0009 later features
  tests/           227 tests (run against PostgreSQL)
scripts/           setup_postgres.ps1 (one-time role + database creation)
docs/              data model, workflows, matching, privacy statement, custody, scheduled jobs
```

Views stay thin; every state change goes through `lostfound/services/`.

## Technology stack

Python 3.12+ (developed on 3.14, CI on 3.12) · Django 6.1 · **PostgreSQL 14+** (developed on 18, CI on 16) with `pg_trgm`, full-text search, GIN indexes and append-only triggers · `psycopg2-binary` (or psycopg 3) · Django Templates, Forms, ORM, auth/groups/permissions · Pillow · `cryptography` (Fernet) · Gmail/SMTP via Django's email backend · HTML/CSS/vanilla JS · Leaflet + OpenStreetMap tiles (optional).

## Setup

### 1. PostgreSQL

Install PostgreSQL (Windows: the EDB installer; Ubuntu: `sudo apt install postgresql postgresql-contrib`). Then in `psql` as the `postgres` superuser:

```sql
CREATE USER lostfound WITH PASSWORD 'choose-a-password';
CREATE DATABASE lostfound OWNER lostfound;
\c lostfound
CREATE EXTENSION IF NOT EXISTS pg_trgm;
ALTER USER lostfound CREATEDB;   -- lets the test runner create its test database
```

Creating the extension needs superuser rights, so do it here; the migration's `CREATE EXTENSION IF NOT EXISTS` then succeeds. Run it on `template1` too if you want test databases to have it automatically.

### 2. Python environment

```bash
python -m venv venv
venv\Scripts\activate          # Windows   (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

### 3. Configuration

```bash
copy .env.example .env         # macOS/Linux: cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"                                  # -> DJANGO_SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"     # -> FIELD_ENCRYPTION_KEYS
```

Fill in `.env`. **Back up the encryption key**: private details cannot be decrypted without it. Never commit `.env`.

### 4. Database, admin user, run

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000. A superuser has every permission; give real staff the right **group** in *Administration › Users & roles* instead.

Then, as an administrator: add your campus locations (mark the security office as a **custody point**) and categories. To enable the map picker set `CAMPUS_MAP_CENTER_LAT/LNG` in `.env` and add coordinates to locations; no campus coordinates are assumed.

### PostgreSQL only

The project runs on PostgreSQL only (EduRev P09 Track P): search uses PostgreSQL full-text search and `pg_trgm`, and the audit, custody and points tables are protected by append-only triggers. There is no SQLite fallback. If the database is unreachable, `python manage.py check` (and `runserver`) report `lostfound.E002` with the reason, and pages show a "database not available" message instead of crashing.

First-time setup on Windows (asks for the PostgreSQL superuser password once and creates the role and database named in `.env`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_postgres.ps1
python manage.py migrate
```

Keep exactly one `DB_BACKEND` and one `EMAIL_BACKEND` line in `.env`: the first occurrence of a key wins.

### Moving data from an old SQLite database

Older versions could run on SQLite. `import_sqlite_export` copies a `dumpdata` export (`backups/sqlite-export-groups.json` and `backups/sqlite-export-data.json`) into the migrated PostgreSQL database:

```bash
python manage.py import_sqlite_export            # dry run (default): imports, runs every check, rolls back
python manage.py import_sqlite_export --apply    # pg_dump backup to backups/, then import and commit
```

It keeps primary keys, timestamps, password hashes and encrypted details (no re-encryption), only inserts rows, skips rows that are already present and identical (so re-runs are safe), stops on any conflict before writing, refuses removed demo records, and checks counts, links, decryption, reward references, audit history and media files before committing. The SQLite file is opened read-only.

## Scheduled jobs

| Command | When | Purpose |
|---|---|---|
| `python manage.py refresh_matches` | nightly | recompute match suggestions |
| `python manage.py check_ageing` | daily | warn, queue for disposal review, escalate |

See [docs/SCHEDULED_JOBS.md](docs/SCHEDULED_JOBS.md) for Windows Task Scheduler and cron examples. No Celery/Redis is required.

## Accounts and sign-in

| Who | Sign-in | How the account is created |
|---|---|---|
| Students | `/student/login/` | Self-registration at `/student/register/` |
| Teachers | `/teacher/login/` | Self-registration at `/teacher/register/` |
| Lost & Found team, administrators | `/staff/login/`: password, then a 6-digit code emailed to the account owner | Only an administrator can create them, after re-entering their own password. The account starts locked with no password; the owner activates it from a one-time link sent to their own email and chooses a password |

* Each portal accepts only its own account type. `/accounts/login/` lets people choose a portal.
* Staff codes are stored only as a keyed hash in the session, expire (10 min), are attempt-limited (5) and work once.
* Granting Lost & Found team or administrator roles to an existing account also requires the administrator's password. Set `ALLOWED_STAFF_EMAIL_DOMAINS` to require official email addresses for those roles.
* Django's built-in `/admin/` goes through the same two-step staff sign-in.
* Students and teachers can turn on the same emailed sign-in code in their profile.
* Forgot password accepts a username or email and always sends the link to the account's **registered** address; the response never reveals whether an account exists.
* All email (codes, activation, reset links, claim/handover/reward notices) goes through one sender, `lostfound/services/mailer.py`, configured by `EMAIL_*` in `.env` (Gmail SMTP with an App password, or the console backend in development). `python manage.py check_email --send-to you@example.com` tests delivery. Failures are logged without recipients, codes or links.

## Community rewards rules

* Points are awarded only after a confirmed event: a team-verified found report (+10), the item received into custody (+5), the item returned to its verified owner after a completed handover (+40 return bonus), or a reviewer bonus (+5). Values are configurable under Administration → Rewards & badges.
* Each event has a unique ledger reference, so it can never pay twice. Rejected reports earn nothing, and nobody earns return points for an item returned to themselves.
* Ledger entries are never edited or deleted (also enforced by a PostgreSQL trigger); corrections are new, staff-authorised entries and may revoke badges.
* Points are recognition only, not cash, and never affect claims or verification. The leaderboard shows nicknames or first names to signed-in members only, and members can opt out.

## Interface

* **Two shells, one template tree.** Visitors get a public site (header with Browse items / How it works / Sign in / Create account, full footer). Signed-in users get the app shell: role-aware sidebar (a drawer on phones), top bar with section title, board search, notifications and profile menu.
* **Design system** in `static/lostfound/css/app.css`: navy `#14213D`, indigo actions `#4F46E5`, amber accent `#F5B335`, red for lost/urgent, amber for pending, green for returned. Icons are an inline SVG sprite (`partials/icons.html`, `{% icon "name" %}`); items without photos get a category icon placeholder.
* **Screens:** homepage with search and category chips, board with type/category/location/date-range filters, sort and grid/list views, item detail, lost and found report forms with photo preview and a live review panel, member/security/admin dashboards built from live queries, claim and handover screens with a progress track, password reset, and branded 403/404/500 pages.
* **No JavaScript required:** filters, view toggle, forms and menus all work as plain HTML; JavaScript only adds live updates, previews and the mobile drawer.
* Counts on the homepage come from the database. The project ships no sample data; `purge_demo_data` removes rows left by the retired seed command.

## Testing

```bash
python manage.py test lostfound            # against PostgreSQL (recommended)
```

The suite needs PostgreSQL (the test runner creates and drops its own `test_lostfound` database, so the role needs `CREATEDB`, which `scripts/setup_postgres.ps1` grants). Latest results (31 Oct 2026): 227 tests, all passing on PostgreSQL 18 locally and PostgreSQL 16 on GitHub Actions, none skipped. The workflow runs `check`, `makemigrations --check` and the suite on every push (`.github/workflows/ci.yml`).

## Security and privacy design

* `PrivateDetail` is a separate model, encrypted with Fernet (`FIELD_ENCRYPTION_KEYS`, rotation via `rotate_encryption_keys`), with **no reverse accessor** from `Item` and no `objects` manager.
* The public board, item page and JSON endpoint build frozen `PublicItem` objects from an explicit field allow-list (`services/public.py`); public templates never receive model instances. Tests check every item state, anonymous and logged-in, including searching *for* the secret.
* Verification never auto-approves; failures don't reveal which answer was wrong; attempts and request rates are limited.
* Handover OTPs are stored only as salted hashes, expire, are attempt-limited and single-use, and are never logged. ID numbers and ID images are not stored.
* Custody records and audit entries are append-only (ORM + PostgreSQL trigger). The audit helper refuses keys such as `otp`, `answer`, `password`.
* Uploads: decoded by Pillow, format allow-list, size and pixel limits, re-encoded with random names; proof/signature/disposal images live outside `MEDIA_ROOT` and are served only through permission-checked views.
* CSRF, secure cookies and HSTS when `DJANGO_DEBUG=False`, login rate limiting, `X-Frame-Options: DENY`.

The field-by-field public/private statement for the security office is in [docs/PRIVACY.md](docs/PRIVACY.md).

## Deployment notes

Set `DJANGO_DEBUG=False`, a long `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`; run `collectstatic`; serve with gunicorn/waitress behind HTTPS. Serve `MEDIA_ROOT` (public item photos only) from the web server or object storage; **never** expose `PRIVATE_MEDIA_ROOT`. Use a shared cache (`CACHE_BACKEND=database` + `createcachetable`) when running several processes so rate limits and the board cache are shared. `BOARD_CACHE_SECONDS` (default 60, `0` disables) controls public board caching.

## Specification coverage (EduRev P09, Track P)

| Requirement | Status |
|---|---|
| M1–M8 modules | Implemented (see the table above) |
| PostgreSQL full-text search + `pg_trgm` matching, Pillow EXIF stripping | Implemented |
| Private fields encrypted, separate model/manager, public DTO; exhaustive no-leak test across item states | Implemented |
| Match suggestions on creation and nightly refresh; ≥70% of seeded pairs suggested | Implemented and tested |
| Handover recorded with custody chain and proof; handover requires the security role | Implemented and tested |
| Unclaimed items age into the disposal queue at the configured interval | Implemented and tested |
| Notifications: possible match, claim on your found item, verification, handover scheduled, ageing warning | Implemented (in-app and email) |
| Claim requires passing verification before handover | Handover is blocked until ownership is verified. By default security verifies the claim against the hidden details (a deliberate usability choice for genuine owners who forget a detail); the claimant-answered question challenge is implemented but not the default path |
| API endpoints `/items`, `/claims`, `/handovers` | Provided as server-rendered Django pages; only the public board also has a JSON endpoint (`/api/board/`) |

## Current limitations

* No SSO/ERP or SMS integration (email and in-app notifications only).
* Image similarity matching is not implemented (the specification marks it optional).
* Only the public board has a JSON API; other features are server-rendered pages.
* The UI was checked in desktop Chrome and a 375 px phone viewport; not yet on physical phones, Safari or with a screen reader.
* Rate limiting and the board cache use the local-memory cache by default (per process); use `CACHE_BACKEND=database` for several processes.
* Scheduled jobs (`refresh_matches`, `check_ageing`) must be added to Task Scheduler or cron by the deployer.

## Future scope

Campus SSO, SMS notifications, optional image similarity, a fuller REST API, and object storage for photos.
