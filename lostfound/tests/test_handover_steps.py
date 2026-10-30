"""Three-step handover page: confirm claimant → verify emailed collection code → complete."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from lostfound.models import AuditEntry, Claim, CustodyRecord, Handover, Item, PointsLedgerEntry
from lostfound.services import custody
from lostfound.services.errors import WorkflowError

from .base import LFTestCase


def code_in(message):
    return re.search(r"\b(\d{6})\b", message.body).group(1)


class HandoverStepTests(LFTestCase):
    def setUp(self):
        super().setUp()
        self.handover = self.scheduled_handover()
        self.url = reverse("security_handover", args=[self.handover.pk])
        self.client.force_login(self.guard)

    def post(self, **data):
        return self.client.post(self.url, data, follow=True)

    def send_code(self):
        mail.outbox.clear()
        self.post(action="send_code")
        self.assertEqual(len(mail.outbox), 1)
        return code_in(mail.outbox[0])

    def test_page_shows_three_steps_and_claimant_details(self):
        r = self.client.get(self.url)
        for text in ("Confirm the claimant", "Verify the collection code", "Complete handover", "Complete Handover",
                     "student@uni.test", self.handover.claim.item.reference, "Security Office"):
            self.assertContains(r, text)
        self.assertContains(r, "disabled")  # complete button locked until the code is verified

    def test_code_goes_to_registered_email_and_never_to_page_or_audit(self):
        with self.assertLogs("lostfound", level="INFO") as logs:
            code = self.send_code()
        self.assertEqual(mail.outbox[0].to, ["student@uni.test"])
        self.assertNotContains(self.client.get(self.url), code)
        self.assertNotIn(code, " ".join(logs.output))
        for entry in AuditEntry.objects.filter(action__startswith="handover."):
            self.assertNotIn(code, entry.summary + str(entry.metadata))
        self.assertNotIn(code, Handover.objects.get(pk=self.handover.pk).otp_hash)

    def test_wrong_code_then_right_code_then_complete(self):
        code = self.send_code()
        wrong = "000000" if code != "000000" else "111111"
        r = self.post(action="verify", code=wrong)
        self.assertContains(r, "That code is incorrect")
        r = self.post(action="complete", id_type="student_id", id_name_matches="on")
        self.assertContains(r, "Verify the claimant")
        self.assertEqual(Item.objects.get(pk=self.handover.claim.item_id).status, Item.Status.AWAITING_HANDOVER)
        r = self.post(action="verify", code=code)
        self.assertContains(r, "Code verified")
        r = self.post(action="complete", id_type="student_id", id_name_matches="on")
        self.assertContains(r, "is now Returned")
        item = Item.objects.get(pk=self.handover.claim.item_id)
        self.assertEqual(item.status, Item.Status.RETURNED)
        self.assertEqual(Claim.objects.get(pk=self.handover.claim_id).status, Claim.Status.COMPLETED)
        self.assertTrue(CustodyRecord.objects.filter(item=item, action=CustodyRecord.Action.RELEASED).exists())
        self.assertContains(self.client.get(self.url), "Handover complete: item returned")

    def test_code_is_single_use_and_duplicate_completion_awards_once(self):
        code = self.send_code()
        self.post(action="verify", code=code)
        self.post(action="complete", id_type="student_id", id_name_matches="on")
        self.post(action="complete", id_type="student_id", id_name_matches="on")
        with self.assertRaises(WorkflowError):
            custody.complete_handover(self.handover.pk, self.guard, id_type="student_id", id_name_matches=True,
                                      otp_code=code)
        self.assertEqual(PointsLedgerEntry.objects.filter(user=self.finder, category="return").count(), 1)

    def test_new_code_invalidates_old_one_and_expiry_is_enforced(self):
        first = self.send_code()
        second = self.send_code()
        if first != second:
            self.assertContains(self.post(action="verify", code=first), "incorrect")
        with mock.patch("lostfound.services.custody.timezone.now", return_value=timezone.now() + timedelta(hours=2)):
            ok, msg = custody.verify_collection_code(self.handover.pk, self.guard, second)
        self.assertFalse(ok)
        self.assertIn("expired", msg)

    def test_id_confirmation_still_required(self):
        code = self.send_code()
        self.post(action="verify", code=code)
        self.post(action="complete", id_type="student_id")
        self.assertEqual(Item.objects.get(pk=self.handover.claim.item_id).status, Item.Status.AWAITING_HANDOVER)

    def test_only_authorised_staff_can_send_verify_or_complete(self):
        for user in (self.other, self.student, self.finder):
            self.client.force_login(user)
            for action in ("send_code", "verify", "complete"):
                r = self.client.post(self.url, {"action": action, "code": "123456", "id_type": "student_id",
                                                "id_name_matches": "on"})
                self.assertEqual(r.status_code, 403, (user, action))
        with self.assertRaises(PermissionDenied):
            custody.email_collection_code(self.handover.pk, self.student)
        with self.assertRaises(PermissionDenied):
            custody.verify_collection_code(self.handover.pk, self.other, "123456")
        self.assertEqual(Item.objects.get(pk=self.handover.claim.item_id).status, Item.Status.AWAITING_HANDOVER)

    def test_email_failure_is_reported_without_leaking_the_code(self):
        with mock.patch("lostfound.services.mailer.send_mail", side_effect=OSError("down")):
            r = self.post(action="send_code")
        self.assertContains(r, "could not be emailed".replace("could not be emailed", "could not be sent"))
        self.assertTrue(AuditEntry.objects.filter(action="handover.code_email_failed").exists())

    def test_failure_mid_handover_rolls_everything_back(self):
        code = self.send_code()
        self.post(action="verify", code=code)
        with mock.patch("lostfound.services.rewards.on_handover_completed", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                custody.complete_handover(self.handover.pk, self.guard, id_type="student_id", id_name_matches=True)
        self.assertEqual(Handover.objects.get(pk=self.handover.pk).status, Handover.Status.SCHEDULED)
        self.assertEqual(Item.objects.get(pk=self.handover.claim.item_id).status, Item.Status.AWAITING_HANDOVER)
        self.assertEqual(Claim.objects.get(pk=self.handover.claim_id).status, Claim.Status.APPROVED)
        self.assertFalse(CustodyRecord.objects.filter(item_id=self.handover.claim.item_id, action="released").exists())
        ok, _ = custody.complete_handover(self.handover.pk, self.guard, id_type="student_id", id_name_matches=True)
        self.assertTrue(ok)  # retry after the failure succeeds, with the verified code still valid
        self.assertEqual(PointsLedgerEntry.objects.filter(user=self.finder, category="return").count(), 1)
