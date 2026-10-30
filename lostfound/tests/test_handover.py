from datetime import timedelta
import base64
import io

from django.core.exceptions import PermissionDenied
from django.utils import timezone
from PIL import Image, ImageDraw

from lostfound.models import Claim, CustodyRecord, Handover, Item
from lostfound.services import claims, custody
from lostfound.services.errors import WorkflowError

from .base import LFTestCase, upload


def signature_data_url(blank=False):
    img = Image.new("RGBA", (300, 120), (255, 255, 255, 0))
    if not blank:
        ImageDraw.Draw(img).line([(10, 60), (290, 70), (150, 20)], fill=(20, 30, 60, 255), width=5)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class HandoverTests(LFTestCase):
    def complete(self, handover, actor=None, **kw):
        args = dict(id_type="student_id", id_name_matches=True)
        args.update(kw)
        return custody.complete_handover(handover.pk, actor or self.guard, **args)

    def test_cannot_schedule_before_approval(self):
        claim = self.verified_claim()
        with self.assertRaises(WorkflowError):
            custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office, self.guard)

    def test_student_cannot_schedule_or_complete(self):
        handover = self.scheduled_handover()
        with self.assertRaises(PermissionDenied):
            self.complete(handover, actor=self.other, otp_code="000000")
        with self.assertRaises(PermissionDenied):
            custody.schedule_handover(handover.claim_id, self.student, timezone.now() + timedelta(hours=1), self.office, self.guard)

    def test_handover_blocked_if_verification_not_passed(self):
        handover = self.scheduled_handover()
        Claim.objects.filter(pk=handover.claim_id).update(verification_status=Claim.Verification.FAILED)
        code, _ = custody.issue_otp(handover.pk, self.student)
        with self.assertRaisesMessage(WorkflowError, "verification"):
            self.complete(handover, otp_code=code)

    def test_id_check_confirmation_required(self):
        handover = self.scheduled_handover()
        code, _ = custody.issue_otp(handover.pk, self.student)
        with self.assertRaises(WorkflowError):
            self.complete(handover, otp_code=code, id_name_matches=False)

    def test_otp_success_completes_custody_chain(self):
        lost = self.lost_item()
        item = self.found_item()
        claim = claims.submit_claim(item, self.student, "This is my backpack from the reading hall.", lost_report=lost, with_challenge=True)
        from lostfound.services import verification
        verification.submit_attempt(claim.challenge.pk, self.student, self.correct_answers(claim))
        claims.approve_claim(claim.pk, self.guard, reason="Verified at the desk", verified_in_person=True)
        handover = custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office, self.guard)
        code, _ = custody.issue_otp(handover.pk, self.student)
        handover.refresh_from_db()
        self.assertNotEqual(handover.otp_hash, code)
        self.assertNotIn(code, handover.otp_hash)
        ok, msg = self.complete(handover, otp_code=code, proof_photo=upload())
        self.assertTrue(ok, msg)
        item.refresh_from_db(); lost.refresh_from_db(); claim.refresh_from_db(); handover.refresh_from_db()
        self.assertEqual((item.status, lost.status, claim.status), (Item.Status.RETURNED, Item.Status.RETURNED, Claim.Status.COMPLETED))
        self.assertEqual(handover.status, Handover.Status.COMPLETED)
        self.assertEqual(handover.otp_hash, "")
        self.assertTrue(handover.proof_photo)
        actions = list(CustodyRecord.objects.filter(item=item).values_list("action", flat=True))
        self.assertEqual(actions, ["received", "released"])

    def test_otp_is_single_use(self):
        handover = self.scheduled_handover()
        code, _ = custody.issue_otp(handover.pk, self.student)
        self.assertTrue(self.complete(handover, otp_code=code)[0])
        with self.assertRaises(WorkflowError):
            self.complete(handover, otp_code=code)

    def test_otp_expiry(self):
        handover = self.scheduled_handover()
        code, _ = custody.issue_otp(handover.pk, self.student)
        Handover.objects.filter(pk=handover.pk).update(otp_expires_at=timezone.now() - timedelta(seconds=1))
        ok, msg = self.complete(handover, otp_code=code)
        self.assertFalse(ok)
        self.assertIn("expired", msg)

    def test_otp_attempt_limit_is_persisted(self):
        handover = self.scheduled_handover()
        code, _ = custody.issue_otp(handover.pk, self.student)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(self.cfg.otp_max_attempts):
            ok, msg = self.complete(handover, otp_code=wrong)
            self.assertFalse(ok)
        handover.refresh_from_db()
        self.assertEqual(handover.otp_attempts, self.cfg.otp_max_attempts)
        ok, msg = self.complete(handover, otp_code=code)
        self.assertFalse(ok)
        self.assertIn("Too many", msg)

    def test_only_claimant_can_generate_code(self):
        handover = self.scheduled_handover()
        with self.assertRaises(PermissionDenied):
            custody.issue_otp(handover.pk, self.other)

    def test_code_page_is_not_cached_and_code_not_logged(self):
        from lostfound.models import AuditEntry
        handover = self.scheduled_handover()
        self.client.force_login(self.student)
        r = self.client.post(f"/my/claims/{handover.claim_id}/handover-code/")
        self.assertIn("no-store", r["Cache-Control"])
        code = r.context["code"]
        for e in AuditEntry.objects.all():
            self.assertNotIn(code, e.summary + str(e.metadata))

    def test_signature_method(self):
        handover = self.scheduled_handover(method="signature")
        with self.assertRaisesMessage(WorkflowError, "empty"):
            self.complete(handover, signature_data=signature_data_url(blank=True))
        ok, _ = self.complete(handover, signature_data=signature_data_url())
        self.assertTrue(ok)
        handover.refresh_from_db()
        self.assertTrue(handover.signature_image.name.endswith(".png"))
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(f"/security/files/signature/{handover.pk}/").status_code, 200)

    def test_completed_handover_db_constraint(self):
        from django.db import IntegrityError, transaction
        handover = self.scheduled_handover()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Handover.objects.filter(pk=handover.pk).update(status="completed")
