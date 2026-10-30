"""Startup checks: PostgreSQL must be reachable and have the extensions Track P relies on.

These run with every management command (runserver, migrate, test...), so a missing or
misconfigured database is reported clearly instead of surfacing as a stack trace later."""
from django.conf import settings
from django.core.checks import Error, Warning, register
from django.db import DatabaseError, connection


def describe_target():
    db = settings.DATABASES["default"]
    return f"database '{db.get('NAME')}' at {db.get('HOST') or 'localhost'}:{db.get('PORT') or '5432'} as user '{db.get('USER')}'"


@register()
def postgres_available(app_configs, **kwargs):
    if connection.vendor != "postgresql":
        return [Error("This project runs on PostgreSQL only (EduRev P09 Track P).",
                      hint="Set DB_BACKEND=postgres and the POSTGRES_* values in .env.", id="lostfound.E001")]
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            has_trgm = cur.fetchone() is not None
            cur.execute("SELECT to_regclass('django_migrations') IS NOT NULL")
            migrated = cur.fetchone()[0]
    except DatabaseError as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return [Error(f"Cannot connect to PostgreSQL {describe_target()}: {first_line}",
                      hint="Start the PostgreSQL service and check POSTGRES_* in .env "
                           "(first-time setup: scripts\\setup_postgres.ps1). There is no SQLite fallback.",
                      id="lostfound.E002")]
    if migrated and not has_trgm:
        return [Warning("The pg_trgm extension is not installed, so fuzzy matching and search are degraded.",
                        hint="Run: python manage.py migrate (or CREATE EXTENSION pg_trgm as the database owner).",
                        id="lostfound.W001")]
    return []
