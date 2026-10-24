# Campus Lost & Found Platform

**EduRev P09 — Campus Lost and Found Platform with Ownership Verification and Custody Chain (Track P: Python + Django + PostgreSQL).**

One place to report lost and found items on campus. Finders record identifying details that are **never shown publicly**; a claimant must answer questions drawn from those hidden details, campus security reviews every claim, and every handover is supervised and recorded in an append-only custody chain.

## Features

| Module | What is implemented |
|---|---|
| M1 Item reporting | Separate lost/found flows, mobile-first form, camera capture, photo upload (Pillow resize + EXIF/GPS stripping), category attributes, validation |
| M2 Taxonomy | Two-level categories with public attributes and retention overrides; campus location tree (area › building › floor › room), custody points, searchable selector, optional Leaflet map picker |
| M3 Matching | Weighted, explainable score from category, text (PostgreSQL `pg_trgm`), attributes, location tree/distance and time; suggestions on create/update and a nightly refresh command |
| M4 Private details & verification | Separate `PrivateDetail` model, Fernet-encrypted, no reverse accessor; random challenge, exact/keyword/fuzzy scoring, attempt + rate limits, no hint about which answer failed, audited staff review |
| M5 Claims | Submission, duplicate prevention, competing claims resolved explicitly, row locking + partial unique constraint (only one approved claim per item), reasons visible to claimant only |
| M6 Custody & handover | Receive/transfer/correct (append-only, DB trigger on PostgreSQL), scheduling, OTP (hashed, expiring, attempt-limited, single-use) or drawn signature, ID check confirmation, optional proof photo, receipt |
| M7 Ageing & disposal | Configurable retention, warnings, disposal-review queue, escalation, two-person disposal (security review + authorised decision), protection for claimed/returned items |
| M8 Public board | Redacted board, PostgreSQL full-text + trigram search, filters, sorting, pagination, live filtering, JSON endpoint `/api/board/` |
| Report review | Every new report is reviewed by the Lost & Found team (verify / reject with a reason, duplicate hints). Optional setting holds reports off the board until verified; receiving an item into custody verifies it |
| Community rewards | Append-only points ledger with unique event references (no double awards), configurable policy, achievement tiers and badges, opt-out leaderboard with nicknames, staff-authorised corrections and community awards |

Plus dashboards for students/staff, the Lost & Found team and administrators, analytics from live data, in-app notifications, an audit log, a separate staff sign-in (`/staff/login/`) and full account management (create, edit, roles, deactivate, send reset link).

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
  management/commands/  refresh_matches, check_ageing, seed_demo, rotate_encryption_keys
  migrations/      0001 schema, 0002 PostgreSQL extension/indexes/triggers
  tests/           137 tests
docs/              data model, workflows, matching, privacy statement, custody, scheduled jobs
```

Views stay thin; every state change goes through `lostfound/services/`.

## Technology stack

Python 3.12+ (developed against 3.14 locally, tested on 3.12) · Django 6.1 · PostgreSQL 14+ with `pg_trgm` · Django Templates, Forms, ORM, auth/groups/permissions · Pillow · `cryptography` (Fernet) · HTML/CSS/vanilla JS · Leaflet + OpenStreetMap tiles (optional).

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
python manage.py seed_demo --password "Choose-A-Demo-Pass"   # optional, labelled sample data
python manage.py runserver
```

Open http://127.0.0.1:8000. A superuser has every permission; give real staff the right **group** in *Administration › Users & roles* instead.

Then, as an administrator: add your campus locations (mark the security office as a **custody point**) and categories. To enable the map picker set `CAMPUS_MAP_CENTER_LAT/LNG` in `.env` and add coordinates to locations; no campus coordinates are assumed.

### SQLite fallback (development only)

`DB_BACKEND=sqlite` runs without PostgreSQL. Search becomes simple substring matching, matching uses the built-in Python trigram implementation, and the append-only database triggers are not installed (ORM-level protection still applies). Do not use it in production.

## Scheduled jobs

| Command | When | Purpose |
|---|---|---|
| `python manage.py refresh_matches` | nightly | recompute match suggestions |
| `python manage.py check_ageing` | daily | warn, queue for disposal review, escalate |

See [docs/SCHEDULED_JOBS.md](docs/SCHEDULED_JOBS.md) for Windows Task Scheduler and cron examples. No Celery/Redis is required.

## Community rewards rules

* Points are awarded only after a confirmed event: a team-verified found report (+10), the item received into custody (+5), the item returned to its verified owner (+25), or a reviewer bonus (+5). Values are configurable under Administration → Rewards & badges.
* Each event has a unique ledger reference, so it can never pay twice. Rejected reports earn nothing, and nobody earns return points for an item returned to themselves.
* Ledger entries are never edited or deleted (also enforced by a PostgreSQL trigger); corrections are new, staff-authorised entries and may revoke badges.
* Points are recognition only, not cash, and never affect claims or verification. The leaderboard shows nicknames or first names to signed-in members only, and members can opt out.

## Interface

* **Two shells, one template tree.** Visitors get a public site (header with Browse items / How it works / Sign in / Create account, full footer). Signed-in users get the app shell: role-aware sidebar (a drawer on phones), top bar with section title, board search, notifications and profile menu.
* **Design system** in `static/lostfound/css/app.css`: navy `#14213D`, indigo actions `#4F46E5`, amber accent `#F5B335`, red for lost/urgent, amber for pending, green for returned. Icons are an inline SVG sprite (`partials/icons.html`, `{% icon "name" %}`); items without photos get a category icon placeholder.
* **Screens:** homepage with search and category chips, board with type/category/location/date-range filters, sort and grid/list views, item detail, lost and found report forms with photo preview and a live review panel, member/security/admin dashboards built from live queries, claim and handover screens with a progress track, password reset, and branded 403/404/500 pages.
* **No JavaScript required:** filters, view toggle, forms and menus all work as plain HTML; JavaScript only adds live updates, previews and the mobile drawer.
* Counts on the homepage come from the database. If `seed_demo` sample rows exist, the page says it includes labelled sample data.

## Testing

```bash
python manage.py test lostfound            # against PostgreSQL (recommended)
set DB_BACKEND=sqlite && python manage.py test lostfound   # fallback; 3 PostgreSQL-only tests skip
```

Latest results (Oct 2026): 137 tests; locally on SQLite 134 pass and the 3 PostgreSQL-only tests skip. GitHub Actions runs the suite on PostgreSQL 16 for every push; check the Actions tab for the latest result. The workflow runs on every push (`.github/workflows/ci.yml`).

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

Set `DJANGO_DEBUG=False`, a long `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`; run `collectstatic`; serve with gunicorn/waitress behind HTTPS. Serve `MEDIA_ROOT` (public item photos only) from the web server or object storage; **never** expose `PRIVATE_MEDIA_ROOT`. Use a shared cache (`CACHE_BACKEND=database` + `createcachetable`) when running several processes so rate limits are shared. The board can be cached with Django's cache framework.

## Current limitations

* No SSO/ERP, email or SMS integration yet (in-app notifications only; Django's email backend is configured for a future step).
* Image similarity matching is not implemented (the PDF marks it optional).
* The UI was checked in a desktop browser and a 375 px phone viewport (Chrome engine); not yet on physical phones, Safari or with a screen reader.
* Password reset emails use `EMAIL_BACKEND` (console in development); configure SMTP to deliver them for real.
* Rate limiting uses the local-memory cache by default (per process).

## Screenshots

_Placeholders — add real screenshots after running locally._

* Public board — `docs/screenshots/board.png`
* Found-item report with private details — `docs/screenshots/report-found.png`
* Verification review — `docs/screenshots/claim-review.png`
* Handover — `docs/screenshots/handover.png`
