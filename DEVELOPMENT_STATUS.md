# Development status

_Last updated: 23 Oct 2026._

## Done and verified
* All eight P09 modules (M1–M8) implemented with Django templates, forms, ORM, groups/permissions, PostgreSQL and Pillow.
* All 24 required screens render for their roles (checked by automated rendering tests and a role-by-role smoke run).
* Migrations apply on PostgreSQL 16 (pg_trgm, GIN indexes, append-only triggers) and on SQLite; `makemigrations --check` is clean.
* Test suite: 96 tests pass on PostgreSQL 16; 93 pass + 3 PostgreSQL-only skipped on SQLite.
* `manage.py check --deploy` with production settings: only the optional HSTS-preload warning remains (plus a key warning when using a dummy key).
* Management commands `refresh_matches`, `check_ageing`, `seed_demo`, `rotate_encryption_keys` run successfully.

## Not yet verified
* Manual testing in a real browser and on phones (camera capture, signature pad, Leaflet map with real campus coordinates).
* The GitHub Actions workflow has been written but not yet run on GitHub.
* Performance with large data volumes.

## Possible next steps
1. Run locally, click through each role with `seed_demo`, add real screenshots to the README.
2. Enter real campus locations/coordinates and categories; tune the match threshold on real data.
3. Optional integrations: email notifications (backend already configurable), SSO, object storage for photos, image similarity.
