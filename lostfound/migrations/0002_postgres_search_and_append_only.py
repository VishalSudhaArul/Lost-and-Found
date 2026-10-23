"""PostgreSQL-only features. Each step checks the database vendor so the
documented SQLite development fallback still migrates (without them).

* pg_trgm extension + trigram GIN index on Item.search_text (fuzzy search, matching)
* GIN full-text index on title/search_text
* Triggers making CustodyRecord and AuditEntry append-only at database level
"""
from django.db import migrations

FORWARD = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
    "CREATE INDEX IF NOT EXISTS item_search_text_trgm ON lostfound_item USING gin (search_text gin_trgm_ops);",
    """CREATE INDEX IF NOT EXISTS item_fulltext ON lostfound_item USING gin (
           (setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(search_text, '')), 'B')));""",
    """CREATE OR REPLACE FUNCTION lostfound_forbid_change() RETURNS trigger AS $$
       BEGIN
           RAISE EXCEPTION '% is append-only; record a correction instead', TG_TABLE_NAME;
       END;
       $$ LANGUAGE plpgsql;""",
    """CREATE TRIGGER custodyrecord_append_only BEFORE UPDATE OR DELETE ON lostfound_custodyrecord
       FOR EACH ROW EXECUTE FUNCTION lostfound_forbid_change();""",
    """CREATE TRIGGER auditentry_append_only BEFORE UPDATE OR DELETE ON lostfound_auditentry
       FOR EACH ROW EXECUTE FUNCTION lostfound_forbid_change();""",
]

BACKWARD = [
    "DROP TRIGGER IF EXISTS auditentry_append_only ON lostfound_auditentry;",
    "DROP TRIGGER IF EXISTS custodyrecord_append_only ON lostfound_custodyrecord;",
    "DROP FUNCTION IF EXISTS lostfound_forbid_change();",
    "DROP INDEX IF EXISTS item_fulltext;",
    "DROP INDEX IF EXISTS item_search_text_trgm;",
]


def _run(statements):
    def inner(apps, schema_editor):
        if schema_editor.connection.vendor != "postgresql":
            return
        for sql in statements:
            schema_editor.execute(sql, params=None)
    return inner


class Migration(migrations.Migration):
    dependencies = [("lostfound", "0001_initial")]
    operations = [migrations.RunPython(_run(FORWARD), _run(BACKWARD))]
