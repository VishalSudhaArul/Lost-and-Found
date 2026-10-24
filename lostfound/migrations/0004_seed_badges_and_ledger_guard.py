"""Seeds the default achievement tiers and badges (editable by campus
administrators afterwards) and, on PostgreSQL, makes the points ledger
append-only at database level like the custody and audit tables."""
from django.db import migrations

TIERS = [
    ("helpful-neighbor", "Helpful Neighbor", "Your first verified contribution to a campus return.", 1, "bronze"),
    ("campus-helper", "Campus Helper", "5 verified helpful actions.", 5, "silver"),
    ("trust-builder", "Trust Builder", "10 verified helpful actions.", 10, "gold"),
    ("community-champion", "Community Champion", "25 verified helpful actions.", 25, "platinum"),
    ("kindness-ambassador", "Kindness Ambassador", "50 verified helpful actions.", 50, "indigo"),
]
BADGES = [
    ("first-found-report", "First Found Report", "Your first found-item report verified by the Lost & Found team.",
     "found_reports", 1, "green"),
    ("successful-return", "Successful Return", "An item you found was returned to its verified owner.", "returns", 1, "gold"),
    ("five-verified", "Five Verified Contributions", "Five verified helpful actions on campus.", "verified_actions", 5, "silver"),
    ("consistent-helper", "Consistent Helper", "Three items you found reached their owners.", "returns", 3, "platinum"),
]


def seed(apps, schema_editor):
    Badge = apps.get_model("lostfound", "AchievementBadge")
    for code, name, desc, threshold, style in TIERS:
        Badge.objects.get_or_create(code=code, defaults=dict(name=name, description=desc, kind="tier",
                                                             metric="verified_actions", threshold=threshold, style=style))
    for code, name, desc, metric, threshold, style in BADGES:
        Badge.objects.get_or_create(code=code, defaults=dict(name=name, description=desc, kind="badge",
                                                             metric=metric, threshold=threshold, style=style))


def unseed(apps, schema_editor):
    apps.get_model("lostfound", "AchievementBadge").objects.filter(
        code__in=[t[0] for t in TIERS] + [b[0] for b in BADGES], holders__isnull=True).delete()


def guard(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("""CREATE TRIGGER pointsledger_append_only BEFORE UPDATE OR DELETE
            ON lostfound_pointsledgerentry FOR EACH ROW EXECUTE FUNCTION lostfound_forbid_change();""")


def unguard(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP TRIGGER IF EXISTS pointsledger_append_only ON lostfound_pointsledgerentry;")


class Migration(migrations.Migration):
    dependencies = [("lostfound", "0003_review_and_rewards")]
    operations = [migrations.RunPython(seed, unseed), migrations.RunPython(guard, unguard)]
