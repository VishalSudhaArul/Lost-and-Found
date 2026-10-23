from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from unittest import skipUnless

from lostfound.models import AuditEntry, CustodyRecord
from lostfound.services import audit, custody
from lostfound.services.errors import WorkflowError

from .base import LFTestCase


class CustodyAndAuditTests(LFTestCase):
    def test_receive_requires_custody_point_and_permission(self):
        item = self.found_item(receive=False)
        with self.assertRaises(WorkflowError):
            custody.receive_item(item.pk, self.guard, self.library)
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            custody.receive_item(item.pk, self.student, self.office)

    def test_transfer_and_history(self):
        from lostfound.models import Location
        locker = Location.objects.create(name="Locker B", parent=self.campus, is_custody_point=True)
        item = self.found_item()
        custody.transfer_item(item.pk, self.guard, locker, "Moved overnight")
        item.refresh_from_db()
        self.assertEqual(item.current_custody_location, locker)
        records = list(CustodyRecord.objects.filter(item=item))
        self.assertEqual([r.action for r in records], ["received", "transferred"])
        self.assertEqual(records[1].from_location, self.office)

    def test_custody_records_are_append_only_in_orm(self):
        item = self.found_item()
        record = CustodyRecord.objects.get(item=item)
        record.notes = "tampered"
        with self.assertRaises(ValidationError):
            record.save()
        with self.assertRaises(ValidationError):
            record.delete()

    def test_correction_appends_without_editing_original(self):
        item = self.found_item()
        original = CustodyRecord.objects.get(item=item)
        custody.correct_record(original.pk, self.guard, "Received by guard2, not guard")
        original.refresh_from_db()
        self.assertEqual(original.notes, "")
        self.assertEqual(CustodyRecord.objects.filter(corrects=original).count(), 1)

    @skipUnless(connection.vendor == "postgresql", "database trigger exists only on PostgreSQL")
    def test_database_trigger_blocks_update_and_delete(self):
        item = self.found_item()
        for sql in ("UPDATE lostfound_custodyrecord SET notes = 'x'", "DELETE FROM lostfound_custodyrecord",
                    "UPDATE lostfound_auditentry SET summary = 'x'"):
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(sql)
        self.assertTrue(CustodyRecord.objects.filter(item=item).exists())

    def test_audit_entries_append_only_and_refuse_secrets(self):
        entry = audit.record(self.guard, "test.action", "Something happened")
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValueError):
            audit.record(self.guard, "test.bad", "x", otp="123456")
        with self.assertRaises(ValueError):
            audit.record(self.guard, "test.bad", "x", answer="zebra")
        self.assertEqual(AuditEntry.objects.filter(action="test.bad").count(), 0)
