"""Import the SQLite-era JSON export into PostgreSQL, safely.

    python manage.py import_sqlite_export            # dry run (default): full import + checks, then rolled back
    python manage.py import_sqlite_export --apply    # pg_dump backup first, then import and commit

Rules
* Records are only ever INSERTED with their original primary keys. Nothing is updated,
  deleted or truncated, so the append-only triggers stay enabled.
* A record already present with identical content is skipped (seeded groups and badges,
  or everything on a re-run). Same key with different content, or a unique value
  (username, reference, ...) owned by another row, is a conflict: the command stops and
  nothing is written.
* Removed demo data (seed accounts / "[Sample] " items) is refused.
* Before committing it checks counts, links, password hashes, decryption of every
  encrypted field, reward-ledger references, audit history and media files. Any failure
  rolls the whole import back.
* Output shows model names, counts and primary keys only: never passwords, hashes,
  private details, codes, emails or keys."""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from glob import glob
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.db.models.fields import AutoFieldMixin

from .purge_demo_data import SEED_USERNAMES

ITEM_REFERENCE_CATEGORIES = {"report", "custody", "return", "bonus"}


class DryRun(Exception):
    pass


def _canon(value):
    """Comparable form of a serialized field value (m2m lists are order-insensitive)."""
    if isinstance(value, list):
        return sorted(json.dumps(v, sort_keys=True) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return value


def _serialized(instance, natural_primary=False):
    data = json.loads(serializers.serialize("json", [instance], use_natural_foreign_keys=True,
                                            use_natural_primary_keys=natural_primary))[0]
    return data["fields"]


def find_pg_dump():
    found = shutil.which("pg_dump")
    if found:
        return found
    candidates = sorted(glob(r"C:\Program Files\PostgreSQL\*\bin\pg_dump.exe"), reverse=True)
    return candidates[0] if candidates else None


def backup_postgres(backup_dir):
    """Timestamped custom-format pg_dump. The password goes to pg_dump via its environment only."""
    exe = find_pg_dump()
    if not exe:
        raise CommandError("pg_dump not found; cannot take the required backup. Nothing was imported.")
    db = connection.settings_dict
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pg-before-import-{datetime.now():%Y%m%d-%H%M%S}.dump"
    env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
    result = subprocess.run([exe, "-Fc", "-h", db.get("HOST") or "localhost", "-p", str(db.get("PORT") or 5432),
                             "-U", db["USER"], "-f", str(target), db["NAME"]],
                            env=env, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise CommandError(f"pg_dump failed (exit {result.returncode}); nothing was imported.")
    return target


class Command(BaseCommand):
    help = "Import backups/sqlite-export-*.json into PostgreSQL (dry run unless --apply)."

    def add_arguments(self, parser):
        base = Path(settings.BASE_DIR)
        parser.add_argument("--data", default=str(base / "backups" / "sqlite-export-data.json"))
        parser.add_argument("--groups", default=str(base / "backups" / "sqlite-export-groups.json"))
        parser.add_argument("--sqlite", default=str(base / "db.sqlite3"),
                            help="Source SQLite file, opened read-only to confirm the export matches it ('' to skip).")
        parser.add_argument("--backup-dir", default=str(base / "backups"))
        parser.add_argument("--apply", action="store_true", help="Back up PostgreSQL, then import and commit.")

    # --- helpers -----------------------------------------------------------------
    def say(self, msg, style=None):
        self.stdout.write(style(msg) if style else msg)

    def load(self, path):
        p = Path(path)
        if not p.exists():
            raise CommandError(f"Export file not found: {p}")
        raw = p.read_text(encoding="utf-8")
        return raw, json.loads(raw), hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def check_source(self, sqlite_path, records):
        """Every exported model's row count must equal the SQLite table (opened read-only)."""
        if not sqlite_path:
            self.say("Source SQLite check skipped.")
            return
        p = Path(sqlite_path)
        if not p.exists():
            raise CommandError(f"SQLite file not found: {p}")
        src = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            counts = Counter(r["model"] for r in records)
            for label, n in sorted(counts.items()):
                table = apps.get_model(label)._meta.db_table
                try:
                    have = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                except sqlite3.Error:
                    raise CommandError(f"{p.name} has no readable {table} table; it is not the source of this export.")
                if have != n:
                    raise CommandError(f"Export does not match {p.name}: {label} has {n} exported rows but "
                                       f"{have} in SQLite. Re-export before importing.")
        finally:
            src.close()
        self.say(f"Source check: export matches {p.name} ({sum(counts.values())} rows, opened read-only).")

    def check_demo(self, records):
        bad = []
        for r in records:
            f = r["fields"]
            if r["model"] == "auth.user" and f.get("username") in SEED_USERNAMES and f.get("last_name") == "(sample)" \
                    and str(f.get("email", "")).endswith("@example.edu"):
                bad.append(f"auth.user #{r['pk']}")
            if r["model"] == "lostfound.item" and str(f.get("title", "")).startswith("[Sample] "):
                bad.append(f"lostfound.item #{r['pk']}")
        if bad:
            raise CommandError(f"The export contains removed demo records ({', '.join(bad)}). Stopped; nothing written.")

    def unique_conflicts(self, model, record):
        """Unique values in the record already owned by a *different* row."""
        found = []
        f = record["fields"]
        lookups = [{fld.name: f[fld.name]} for fld in model._meta.fields
                   if fld.unique and not fld.primary_key and fld.name in f and not isinstance(fld, models.ForeignKey)]
        for c in model._meta.constraints:
            if isinstance(c, models.UniqueConstraint) and c.condition is None and c.fields \
                    and all(n in f and not isinstance(model._meta.get_field(n), models.ForeignKey) for n in c.fields):
                lookups.append({n: f[n] for n in c.fields})
        for lookup in lookups:
            qs = model._base_manager.filter(**lookup)
            if record.get("pk") is not None:
                qs = qs.exclude(pk=record["pk"])
            if qs.exists():
                found.append("/".join(lookup))
        return found

    # --- main --------------------------------------------------------------------
    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            raise CommandError("Target must be PostgreSQL.")
        from django.db.migrations.executor import MigrationExecutor
        if MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes()):
            raise CommandError("PostgreSQL has unapplied migrations. Run `python manage.py migrate` first.")

        groups_raw, groups, groups_hash = self.load(opts["groups"])
        data_raw, data, data_hash = self.load(opts["data"])
        self.say(f"Mode: {'APPLY' if opts['apply'] else 'DRY RUN (nothing is kept)'}")
        self.say(f"Export files: groups sha256:{groups_hash}…, data sha256:{data_hash}…")
        self.check_source(opts["sqlite"], groups + data)
        self.check_demo(groups + data)

        # Classify every record: identical (skip), missing (insert) or conflict (stop).
        plan, conflicts = defaultdict(Counter), []
        to_insert = []
        for raw, natural in ((groups_raw, True), (data_raw, False)):
            for record, obj in zip(json.loads(raw), serializers.deserialize("json", raw, handle_forward_references=True)):
                model = type(obj.object)
                label = model._meta.label_lower
                existing = None
                if natural:
                    existing = model._base_manager.filter(pk=obj.object.pk).first() if obj.object.pk else None
                elif record.get("pk") is not None:
                    existing = model._base_manager.filter(pk=record["pk"]).first()
                if existing is not None:
                    have = _serialized(existing, natural_primary=natural)
                    differs = sorted(k for k, v in record["fields"].items() if _canon(have.get(k)) != _canon(v))
                    if differs:
                        conflicts.append(f"{label} #{record.get('pk') or existing.pk}: differs in {', '.join(differs)}")
                        plan[label]["conflict"] += 1
                    else:
                        plan[label]["identical (skip)"] += 1
                    continue
                clash = self.unique_conflicts(model, record)
                if clash:
                    conflicts.append(f"{label} #{record.get('pk')}: unique value already used by another row "
                                     f"({', '.join(clash)})")
                    plan[label]["conflict"] += 1
                    continue
                plan[label]["insert"] += 1
                to_insert.append((record, obj, natural))

        self.say("\nPlan per model:")
        for label in sorted(plan):
            self.say(f"  {label:34} " + ", ".join(f"{k}: {v}" for k, v in sorted(plan[label].items())))
        if conflicts:
            for c in conflicts:
                self.say(f"  CONFLICT {c}", self.style.ERROR)
            raise CommandError(f"{len(conflicts)} conflict(s). Stopped before writing anything.")
        if not to_insert:
            self.say("\nNothing to import: every record is already present and identical.", self.style.SUCCESS)
            return

        backup = None
        if opts["apply"]:
            backup = backup_postgres(Path(opts["backup_dir"]))
            self.say(f"\nPostgreSQL backup: {backup}")

        try:
            with transaction.atomic():
                # Re-deserialize lazily so each record's natural-key links resolve against the rows
                # inserted just before it (users before items, items before custody, ...).
                built = []
                for natural in (True, False):
                    batch = [r for r, _, n in to_insert if n is natural]
                    if batch:
                        for obj in serializers.deserialize("json", json.dumps(batch), handle_forward_references=True):
                            obj.save()
                            built.append(obj)
                for obj in built:
                    if obj.deferred_fields:  # links to rows that came later in the file
                        obj.save_deferred_fields()
                with connection.cursor() as cur:
                    cur.execute("SET CONSTRAINTS ALL IMMEDIATE")  # surface any broken link now
                self.validate(groups + data)
                if not opts["apply"]:
                    raise DryRun
                self.bump_sequences({type(o.object) for o in built})
        except DryRun:
            self.say(f"\nDry run OK: {len(to_insert)} record(s) would be imported and all checks passed. "
                     "Rolled back; nothing was written.", self.style.WARNING)
            self.say("To import for real: python manage.py import_sqlite_export --apply")
            return
        self.say(f"\nImported {len(to_insert)} record(s). All checks passed.", self.style.SUCCESS)

    # --- validation (inside the transaction) --------------------------------------
    def validate(self, records):
        from django.contrib.auth import get_user_model
        from lostfound.models import PointsLedgerEntry, PrivateDetail, VerificationAttempt
        from lostfound.services import crypto

        problems = []
        by_model = defaultdict(list)
        for r in records:
            by_model[r["model"]].append(r)

        # 1. every exported record is present
        for label, rows in sorted(by_model.items()):
            model = apps.get_model(label)
            if label == "auth.group":
                missing = [r["fields"]["name"] for r in rows if not model.objects.filter(name=r["fields"]["name"]).exists()]
            else:
                pks = [r["pk"] for r in rows]
                present = set(model._base_manager.filter(pk__in=pks).values_list("pk", flat=True))
                missing = [pk for pk in pks if pk not in present and str(pk) not in {str(p) for p in present}]
            if missing:
                problems.append(f"{label}: {len(missing)} record(s) missing after import")

        # 2. password hashes byte-identical (compared, never printed)
        User = get_user_model()
        for r in by_model.get(get_user_model()._meta.label_lower, []):
            if User.objects.filter(pk=r["pk"]).values_list("password", flat=True).first() != r["fields"]["password"]:
                problems.append(f"auth.user #{r['pk']}: password hash changed")

        # 3. group memberships
        for r in by_model.get("auth.user", []):
            want = sorted(g[0] if isinstance(g, list) else g for g in r["fields"].get("groups", []))
            have = sorted(User.objects.get(pk=r["pk"]).groups.values_list("name", flat=True))
            if want != have:
                problems.append(f"auth.user #{r['pk']}: group memberships differ")

        # 4. encrypted fields decrypt with the current keys
        detail_pks = [r["pk"] for r in by_model.get("lostfound.privatedetail", [])]
        attempt_pks = [r["pk"] for r in by_model.get("lostfound.verificationattempt", [])]
        undecryptable = 0
        for d in PrivateDetail.vault.filter(pk__in=detail_pks):
            try:
                d.decrypt_question(), d.decrypt_answer()
            except Exception:
                undecryptable += 1
        for a in VerificationAttempt.objects.filter(pk__in=attempt_pks):
            try:
                crypto.decrypt_json(a.responses_ciphertext)
            except Exception:
                undecryptable += 1
        if undecryptable:
            problems.append(f"{undecryptable} encrypted field(s) cannot be decrypted with FIELD_ENCRYPTION_KEYS")

        # 5. reward ledger references point at the right item
        ledger_pks = [r["pk"] for r in by_model.get("lostfound.pointsledgerentry", [])]
        for e in PointsLedgerEntry.objects.filter(pk__in=ledger_pks):
            prefix, _, suffix = e.reference.partition(":")
            if e.category in ITEM_REFERENCE_CATEGORIES and e.item_id and suffix != str(e.item_id):
                problems.append(f"points ledger #{e.pk}: reference does not match its item")

        # 6. referenced files exist (storage-specific, private ones included)
        missing_files = 0
        for label, rows in by_model.items():
            model = apps.get_model(label)
            for fld in model._meta.fields:
                if isinstance(fld, models.FileField):
                    for r in rows:
                        name = r["fields"].get(fld.name)
                        if name and not fld.storage.exists(name):
                            missing_files += 1
        if missing_files:
            problems.append(f"{missing_files} referenced file(s) missing from storage")

        if problems:
            raise CommandError("Validation failed, everything rolled back:\n  " + "\n  ".join(problems))
        self.say("\nChecks passed:")
        self.say(f"  all {sum(len(v) for v in by_model.values())} exported records present; links valid")
        self.say(f"  {len(by_model.get('auth.user', []))} password hash(es) and group memberships identical")
        self.say(f"  {len(detail_pks)} private detail(s) and {len(attempt_pks)} verification attempt(s) decrypt")
        self.say(f"  {len(ledger_pks)} reward ledger entr(y/ies) reference the right items; "
                 f"{len(by_model.get('lostfound.auditentry', []))} audit entries present; referenced files exist")

    def bump_sequences(self, model_set):
        """Raise each identity/serial sequence to at least MAX(id). Never lowers one."""
        with connection.cursor() as cur:
            for model in model_set:
                pk = model._meta.pk
                if not isinstance(pk, AutoFieldMixin):
                    continue
                table = model._meta.db_table
                cur.execute("SELECT pg_get_serial_sequence(%s, %s)", [table, pk.column])
                seq = cur.fetchone()[0]
                if not seq:
                    continue
                cur.execute(f'SELECT MAX("{pk.column}") FROM "{table}"')
                top = cur.fetchone()[0] or 0
                cur.execute(f"SELECT last_value, is_called FROM {seq}")
                last, called = cur.fetchone()
                current = last if called else last - 1
                if top > current:
                    cur.execute("SELECT setval(%s, %s, true)", [seq, top])
