"""import_sqlite_export: dry run by default, transactional, idempotent, conflict-safe."""
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import Group
from django.core import serializers
from django.core.management import CommandError, call_command
from django.db import transaction
from django.utils import timezone

from lostfound import roles
from lostfound.models import AuditEntry, CustodyRecord, Item, PointsLedgerEntry, PrivateDetail

from .base import LFTestCase, User

EXPORT_MODELS = ["auth.user", "lostfound.userprofile", "lostfound.userrewardprofile", "lostfound.item",
                 "lostfound.privatedetail", "lostfound.custodyrecord", "lostfound.auditentry",
                 "lostfound.pointsledgerentry", "lostfound.userbadge", "lostfound.notification"]
TARGET = "lostfound.management.commands.import_sqlite_export.backup_postgres"


class _Rollback(Exception):
    pass


class ImportSqliteExportTests(LFTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="lf-import-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.groups_path, self.data_path = self.write_export()

    def write_export(self, mutate=None):
        """Create a realistic account + found item + history inside a savepoint, serialize it like
        `dumpdata --natural-foreign`, then roll the savepoint back so the rows are absent here."""
        models = [apps.get_model(m) for m in EXPORT_MODELS]
        before = {m: set(m._base_manager.values_list("pk", flat=True)) for m in models}
        records = []
        try:
            with transaction.atomic():
                finder = self.make_user("importedfinder", roles.MEMBERS)
                self.found_item(title="Imported umbrella", reporter=finder)  # details, custody, points, audit
                for m in models:
                    new = m._base_manager.exclude(pk__in=before[m]).order_by("pk")
                    records += json.loads(serializers.serialize("json", new, use_natural_foreign_keys=True))
                raise _Rollback
        except _Rollback:
            pass
        if mutate:
            records = mutate(records)
        groups = serializers.serialize("json", Group.objects.all(), use_natural_foreign_keys=True,
                                       use_natural_primary_keys=True)
        gp, dp = self.tmp / "groups.json", self.tmp / "data.json"
        gp.write_text(groups, encoding="utf-8")
        dp.write_text(json.dumps(records), encoding="utf-8")
        return gp, dp

    def run_cmd(self, *extra, groups=None, data=None):
        out = StringIO()
        call_command("import_sqlite_export", "--groups", str(groups or self.groups_path),
                     "--data", str(data or self.data_path), "--sqlite", "", "--backup-dir", str(self.tmp),
                     *extra, stdout=out)
        return out.getvalue()

    def exported(self, label):
        return [r for r in json.loads(self.data_path.read_text(encoding="utf-8")) if r["model"] == label]

    def test_dry_run_is_default_and_writes_nothing(self):
        with mock.patch(TARGET) as backup:
            out = self.run_cmd()
        self.assertIn("DRY RUN", out)
        self.assertIn("Dry run OK", out)
        self.assertIn("identical (skip)", out)  # the seeded groups
        backup.assert_not_called()
        self.assertFalse(User.objects.filter(username="importedfinder").exists())
        self.assertFalse(Item.objects.filter(title="Imported umbrella").exists())

    def test_apply_imports_everything_with_original_keys_and_history(self):
        with mock.patch(TARGET, return_value=self.tmp / "x.dump") as backup:
            out = self.run_cmd("--apply")
        backup.assert_called_once()
        self.assertIn("Imported", out)
        user = User.objects.get(username="importedfinder")
        src_user = self.exported("auth.user")[0]
        self.assertEqual(user.pk, src_user["pk"])
        self.assertEqual(user.password, src_user["fields"]["password"])
        self.assertEqual(list(user.groups.values_list("name", flat=True)), [roles.MEMBERS])
        item = Item.objects.get(title="Imported umbrella")
        self.assertEqual(item.pk, self.exported("lostfound.item")[0]["pk"])
        details = PrivateDetail.vault.filter(item=item)
        self.assertEqual(details.count(), 2)
        self.assertTrue(all(d.decrypt_answer() for d in details))
        self.assertEqual(CustodyRecord.objects.filter(item=item).count(), len(self.exported("lostfound.custodyrecord")))
        self.assertEqual(PointsLedgerEntry.objects.filter(user=user).count(), len(self.exported("lostfound.pointsledgerentry")))
        self.assertEqual(AuditEntry.objects.filter(pk__in=[r["pk"] for r in self.exported("lostfound.auditentry")]).count(),
                         len(self.exported("lostfound.auditentry")))
        # sequences were raised: new rows don't collide with imported keys
        fresh = Item.objects.create(report_type="found", title="After import", description="x", category=self.backpacks,
                                    location=self.hall, occurred_at=timezone.now(), reporter=self.finder)
        self.assertGreater(fresh.pk, item.pk)
        self.assertNotIn(src_user["fields"]["password"], out)

    def test_rerun_is_idempotent(self):
        with mock.patch(TARGET, return_value=self.tmp / "x.dump"):
            self.run_cmd("--apply")
            counts = {m: apps.get_model(m)._base_manager.count() for m in EXPORT_MODELS}
            out = self.run_cmd("--apply")
        self.assertIn("Nothing to import", out)
        self.assertEqual(counts, {m: apps.get_model(m)._base_manager.count() for m in EXPORT_MODELS})

    def test_conflicting_existing_row_stops_before_writing(self):
        def clash(records):
            fake = json.loads(serializers.serialize("json", [self.student], use_natural_foreign_keys=True))[0]
            fake["fields"]["email"] = "someone.else@uni.test"
            return records + [fake]
        _, data = self.write_export(clash)
        with mock.patch(TARGET) as backup, self.assertRaisesMessage(CommandError, "conflict"):
            self.run_cmd("--apply", data=data)
        backup.assert_not_called()
        self.assertFalse(User.objects.filter(username="importedfinder").exists())
        self.assertEqual(User.objects.get(pk=self.student.pk).email, "student@uni.test")

    def test_unique_value_owned_by_another_row_is_a_conflict(self):
        def same_username(records):
            for r in records:
                if r["model"] == "auth.user":
                    r["fields"]["username"] = "student"
            return records
        _, data = self.write_export(same_username)
        with self.assertRaisesMessage(CommandError, "conflict"):
            self.run_cmd("--apply", data=data)
        self.assertFalse(Item.objects.filter(title="Imported umbrella").exists())

    def test_removed_demo_records_are_refused(self):
        def demo(records):
            for r in records:
                if r["model"] == "auth.user":
                    r["fields"].update(username="student1", last_name="(sample)", email="student1@example.edu")
            return records
        _, data = self.write_export(demo)
        with self.assertRaisesMessage(CommandError, "demo"):
            self.run_cmd(data=data)

    def test_undecryptable_detail_rolls_everything_back(self):
        def corrupt(records):
            for r in records:
                if r["model"] == "lostfound.privatedetail":
                    r["fields"]["answer_ciphertext"] = "gAAAAA-not-a-valid-token"
            return records
        _, data = self.write_export(corrupt)
        with mock.patch(TARGET, return_value=self.tmp / "x.dump"), self.assertRaisesMessage(CommandError, "decrypt"):
            self.run_cmd("--apply", data=data)
        self.assertFalse(User.objects.filter(username="importedfinder").exists())

    def test_failed_backup_means_no_import(self):
        with mock.patch(TARGET, side_effect=CommandError("pg_dump failed")), self.assertRaises(CommandError):
            self.run_cmd("--apply")
        self.assertFalse(User.objects.filter(username="importedfinder").exists())

    def test_source_mismatch_is_detected(self):
        empty = self.tmp / "empty.sqlite3"
        import sqlite3
        con = sqlite3.connect(empty)
        con.execute("CREATE TABLE auth_user (id integer)")
        con.commit()
        con.close()
        with self.assertRaises(CommandError):
            out = StringIO()
            call_command("import_sqlite_export", "--groups", str(self.groups_path), "--data", str(self.data_path),
                         "--sqlite", str(empty), stdout=out)
