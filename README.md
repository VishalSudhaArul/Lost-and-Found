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

Plus dashboards for students/staff, security and administrators, analytics from live data, in-app notifications, an audit log, user and role management.

## Roles

| Role (Django group) | Can do |
|---|---|
| Students & Staff | Browse, report lost/found items, claim, answer verification, see own reports/claims/notifications |
| Security Personnel | Security dashboard, intake/custody/transfers, add private details, review verification evidence, approve/reject claims, schedule and complete handovers, disposal review |
| Campus Administrator | Categories, locations, retention policy, authorise disposal, analytics, audit log |
| System Administrator | Users and roles, matching weights/thresholds, verification and OTP limits, audit log |

Roles are additive. Permissions are enforced in views (decorators) **and** again in each service function.

## Architecture

```
config/            settings (env-driven), URLs, error handlers
lostfound/
  models/          one file per area; PrivateDetail lives alone in privacy.py
  services/        business rules: matching, verification, claims, custody, disposal, public, analytics, images, crypto
  views/           public, accounts, member, security, administration, errors
  templates/       Django templates (base layout + partials)
  static/          app.css, app.js (progressive enhancement only)
  management/commands/  refresh_matches, check_ageing, seed_demo, rotate_encryption_keys
  migrations/      0001 schema, 0002 PostgreSQL extension/indexes/triggers
  tests/           96 tests
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

## Testing

```bash
python manage.py test lostfound            # against PostgreSQL (recommended)
set DB_BACKEND=sqlite && python manage.py test lostfound   # fallback; 3 PostgreSQL-only tests skip
```

Latest results (Oct 2026): **96 passed on PostgreSQL 16**, 93 passed + 3 skipped on SQLite. GitHub Actions runs the suite against PostgreSQL on every push (`.github/workflows/ci.yml`).

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
* The UI was verified by automated rendering tests, not yet by manual browser/device testing.
* Rate limiting uses the local-memory cache by default (per process).

## Screenshots

_Placeholders — add real screenshots after running locally._

* Public board — `docs/screenshots/board.png`
* Found-item report with private details — `docs/screenshots/report-found.png`
* Verification review — `docs/screenshots/claim-review.png`
* Handover — `docs/screenshots/handover.png`
