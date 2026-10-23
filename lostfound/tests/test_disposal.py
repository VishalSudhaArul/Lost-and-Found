from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from lostfound.models import DisposalRecord, Item, Notification
from lostfound.services import claims, disposal
from lostfound.services.errors import WorkflowError

from .base import LFTestCase


class AgeingAndDisposalTests(LFTestCase):
    def aged_item(self, days=61, **kw):
        item = self.found_item(**kw)
        Item.objects.filter(pk=item.pk).update(custody_since=timezone.now() - timedelta(days=days))
        item.refresh_from_db()
        return item

    def test_items_enter_review_at_configured_interval(self):
        young = self.aged_item(days=10, title="Young")
        old = self.aged_item(days=61, title="Old")
        summary = disposal.run_ageing_check()
        young.refresh_from_db(); old.refresh_from_db()
        self.assertEqual((young.status, old.status), ("in_custody", "disposal_review"))
        self.assertEqual(summary["moved_to_review"], 1)

    def test_warning_and_escalation_are_deduplicated(self):
        warn = self.aged_item(days=55, title="Warn")
        self.aged_item(days=80, title="Overdue")
        disposal.run_ageing_check()
        disposal.run_ageing_check()
        self.assertEqual(Notification.objects.filter(user=self.guard, dedupe_key__startswith=f"ageing-warn:{warn.pk}:").count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.cadmin, dedupe_key__startswith="ageing-escalate:").count(), 1)

    def test_category_retention_override(self):
        self.backpacks.retention_days = 7
        self.backpacks.save()
        item = self.aged_item(days=8)
        disposal.run_ageing_check()
        item.refresh_from_db()
        self.assertEqual(item.status, "disposal_review")

    def test_active_claim_protects_item(self):
        item = self.aged_item()
        claims.submit_claim(item, self.student, "This is mine, I lost it two months ago in the hall.")
        summary = disposal.run_ageing_check()
        item.refresh_from_db()
        self.assertEqual(item.status, "in_custody")
        self.assertEqual(summary["protected_by_claims"], 1)

    def test_disposal_requires_review_permission_and_expiry(self):
        item = self.aged_item()
        disposal.run_ageing_check()
        with self.assertRaisesMessage(WorkflowError, "review"):
            disposal.dispose(item.pk, self.cadmin, "donated", "Unclaimed after 60 days")
        with self.assertRaises(PermissionDenied):
            disposal.dispose(item.pk, self.guard, "donated", "Security cannot authorise alone")
        disposal.confirm_review(item.pk, self.guard)
        record = disposal.dispose(item.pk, self.cadmin, "donated", "Unclaimed after 60 days", recipient="NGO")
        item.refresh_from_db()
        self.assertEqual(item.status, "disposed")
        self.assertEqual((record.reviewed_by, record.authorized_by), (self.guard, self.cadmin))
        self.assertTrue(item.custody_records.filter(action="disposed").exists())

    def test_claim_submitted_during_review_blocks_disposal(self):
        item = self.aged_item()
        disposal.run_ageing_check()
        disposal.confirm_review(item.pk, self.guard)
        claims.submit_claim(item, self.student, "Wait, this is mine! Lost it during exams.")
        with self.assertRaises(WorkflowError):
            disposal.dispose(item.pk, self.cadmin, "donated", "Unclaimed")

    def test_returned_item_cannot_be_disposed(self):
        handover = self.scheduled_handover()
        from lostfound.services import custody
        code, _ = custody.issue_otp(handover.pk, self.student)
        custody.complete_handover(handover.pk, self.guard, id_type="student_id", id_name_matches=True, otp_code=code)
        item = handover.claim.item
        with self.assertRaises(WorkflowError):
            disposal.dispose(item.pk, self.cadmin, "donated", "Should fail")
        self.assertFalse(DisposalRecord.objects.exists())

    def test_extension_returns_item_to_custody(self):
        item = self.aged_item()
        disposal.run_ageing_check()
        disposal.extend_retention(item.pk, self.guard, 30, "Owner phoned")
        item.refresh_from_db()
        self.assertEqual((item.status, item.retention_extension_days), ("in_custody", 30))
        disposal.run_ageing_check()
        item.refresh_from_db()
        self.assertEqual(item.status, "in_custody")
