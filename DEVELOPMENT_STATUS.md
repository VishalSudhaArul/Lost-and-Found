# Development status

_Last updated: 31 Oct 2026._

## Done and verified
* All eight P09 modules (M1–M8) implemented with Django templates, forms, ORM, groups/permissions, PostgreSQL and Pillow.
* All required screens render for their roles (automated per-role rendering tests in `tests/test_ui.py`).
* UI redesign: shared layout partials, public vs. signed-in shells, design system and icon sprite, redesigned homepage, board (date range, grid/list), report forms, dashboards, claim/handover progress, password reset, error pages.
* Checked in a desktop browser and a 375 px phone viewport: no horizontal scrolling; mobile drawer opens and closes with Esc.
* PostgreSQL is the only database (pg_trgm, full-text search, GIN indexes, append-only triggers); `makemigrations --check` and `migrate --check` are clean. No SQLite fallback.
* Lost & Found team report review, community points ledger, tiers/badges, opt-out leaderboard, staff sign-in and administrator account management (tests in `tests/test_rewards.py`).
* Separate student and teacher portals, two-step staff sign-in (password + emailed code), email activation for administrator-created accounts, password re-entry for role escalation, admin overview with complaint pipeline and student/teacher breakdown (tests in `tests/test_portals.py`).
* Staff-led, in-person ownership verification: claims start as Pending review, the claimant visits the desk, an authorised officer records the face-to-face decision (in-person tick + reason, audited, claimant notified). Online question answers are supporting information only and never approve, verify or reject (tests in `tests/test_in_person_verification.py`).
* Real data moved from SQLite to PostgreSQL with `import_sqlite_export` (157 records, all checks passed; originals kept as backups).
* Test suite: 234 tests, all passing on PostgreSQL (local PostgreSQL 18 and GitHub Actions PostgreSQL 16).
* `manage.py check --deploy` with production settings: only the optional HSTS-preload warning remains (plus a key warning when using a dummy key).
* Management commands `refresh_matches`, `check_ageing`, `check_email`, `purge_demo_data`, `import_sqlite_export`, `rotate_encryption_keys` run successfully.

## Not yet verified
* Testing on physical phones, Safari and with a screen reader (camera capture, signature pad, Leaflet map with real campus coordinates).
* Performance with large data volumes.

## Possible next steps
1. Add real screenshots of each role's screens to the README.
2. Enter real campus locations/coordinates and categories; tune the match threshold on real data.
3. Optional integrations: SSO, SMS, object storage for photos, image similarity.
