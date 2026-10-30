"""Staff-led, in-person ownership verification: the portal records the desk officer's decision;
optional online answers never approve or reject a claim."""
from datetime import timedelta

from django.core import mail
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from lostfound.models import AuditEntry, Claim, Item, Notification, VerificationChallenge
from lostfound.services import claims, custody, verification
from lostfound.services.errors import WorkflowError

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase

DESK = {"confirm_compared": "on", "reason": "Owner described the sticker and serial at the desk"}


class InPersonVerificationTests(LFTestCase):
    def submit(self, user=None, item=None):
        return claims.submit_claim(item or self.found_item(), user or self.student, "Black backpack, blue keyring",
                                   where_when="Library", lost_when="Monday")

    def test_new_claim_is_pending_review_and_tells_claimant_to_visit_the_desk(self):
        claim = self.submit()
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)
        self.assertEqual(claim.display_status, "Pending review")
        self.assertEqual(claim.verification_status, Claim.Verification.NOT_STARTED)
        self.assertFalse(VerificationChallenge.objects.filter(claim=claim).exists())  # no quiz to pass
        self.client.force_login(self.student)
        page = self.client.get(reverse("claim_detail", args=[claim.pk]))
        self.assertContains(page, "Pending review")
        self.assertContains(page, "Security Desk with your photo ID")
        for secret in (SECRET_ANSWER, SECRET_SERIAL):
            self.assertNotContains(page, secret)
        self.client.force_login(self.guard)
        self.assertContains(self.client.get(reverse("security_claims")), claim.reference)

    def test_staff_see_private_details_and_record_the_face_to_face_decision(self):
        claim = self.submit()
        self.client.force_login(self.guard)
        review = self.client.get(reverse("security_claim", args=[claim.pk]))
        self.assertContains(review, "In-person verification")
        self.assertContains(review, SECRET_ANSWER)  # staff-only, for questioning at the desk
        self.assertTrue(AuditEntry.objects.filter(action="private_detail.viewed", actor=self.guard).exists())
        mail.outbox.clear()
        self.client.post(reverse("security_claim_approve", args=[claim.pk]), DESK)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.APPROVED)
        self.assertEqual(claim.verification_status, Claim.Verification.PASSED)
        self.assertEqual(claim.decided_by, self.guard)
        self.assertIsNotNone(claim.decided_at)
        self.assertEqual(claim.decision_reason, DESK["reason"])
        entry = AuditEntry.objects.get(action="claim.approved")
        self.assertEqual(entry.actor, self.guard)
        self.assertEqual(entry.metadata["verification"], "in_person")
        self.assertEqual(entry.metadata["reason"], DESK["reason"])
        self.assertTrue(entry.metadata["decided_at"])
        self.assertNotIn(SECRET_ANSWER, entry.summary + str(entry.metadata))
        self.assertTrue(Notification.objects.filter(user=self.student, title="Claim approved").exists())
        self.assertIn(["student@uni.test"], [m.to for m in mail.outbox])

    def test_rejection_after_the_desk_visit_is_recorded_with_reason(self):
        claim = self.submit()
        self.client.force_login(self.guard)
        self.client.post(reverse("security_claim_reject", args=[claim.pk]),
                         {"reason": "Could not describe any identifying feature at the desk"})
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.REJECTED)
        entry = AuditEntry.objects.get(action="claim.rejected")
        self.assertEqual((entry.actor, entry.metadata["verification"]), (self.guard, "in_person"))
        self.assertTrue(Notification.objects.filter(user=self.student, title="Claim not approved").exists())

    def test_approval_needs_the_in_person_tick_and_a_reason(self):
        claim = self.submit()
        self.client.force_login(self.guard)
        self.client.post(reverse("security_claim_approve", args=[claim.pk]), {"reason": DESK["reason"]})
        self.client.post(reverse("security_claim_approve", args=[claim.pk]), {"confirm_compared": "on"})
        self.assertEqual(Claim.objects.get(pk=claim.pk).status, Claim.Status.UNDER_REVIEW)
        with self.assertRaises(WorkflowError):
            claims.approve_claim(claim.pk, self.guard, "", verified_in_person=True)
        with self.assertRaises(WorkflowError):
            claims.approve_claim(claim.pk, self.guard, DESK["reason"])
        self.assertFalse(AuditEntry.objects.filter(action="claim.approved").exists())

    def test_incorrect_online_answers_never_reject_and_correct_ones_never_approve(self):
        wrong = claims.submit_claim(self.found_item(title="Bag A"), self.student, "My black backpack", with_challenge=True)
        for _ in range(wrong.challenge.max_attempts):
            verification.submit_attempt(wrong.challenge.pk, self.student, {})
        wrong.refresh_from_db()
        self.assertEqual(wrong.status, Claim.Status.UNDER_REVIEW)
        self.assertEqual(wrong.verification_status, Claim.Verification.NOT_STARTED)
        right = claims.submit_claim(self.found_item(title="Bag B"), self.other, "My black backpack", with_challenge=True)
        verification.submit_attempt(right.challenge.pk, self.other, self.correct_answers(right))
        right.refresh_from_db()
        self.assertEqual(right.status, Claim.Status.UNDER_REVIEW)
        self.assertNotEqual(right.verification_status, Claim.Verification.PASSED)
        # the genuine owner with wrong online answers is approved after the desk check
        claims.approve_claim(wrong.pk, self.guard, DESK["reason"], verified_in_person=True)
        self.assertEqual(Claim.objects.get(pk=wrong.pk).status, Claim.Status.APPROVED)

    def test_only_authorised_staff_can_decide_or_see_private_details(self):
        claim = self.submit()
        for user in (self.other, self.finder, self.student):
            self.client.force_login(user)
            for name in ("security_claim", "security_claim_approve", "security_claim_reject"):
                r = self.client.post(reverse(name, args=[claim.pk]), {**DESK, "reason": "x" * 10})
                self.assertEqual(r.status_code, 403, (user.username, name))
            with self.assertRaises(PermissionDenied):
                claims.approve_claim(claim.pk, user, DESK["reason"], verified_in_person=True)
        guard_claim = self.submit(user=self.guard, item=self.found_item(title="Guard's bag"))
        with self.assertRaises(PermissionDenied):  # staff can't decide their own claim
            claims.approve_claim(guard_claim.pk, self.guard, DESK["reason"], verified_in_person=True)
        self.assertEqual(Claim.objects.get(pk=claim.pk).status, Claim.Status.UNDER_REVIEW)

    def test_approved_claim_goes_through_supervised_handover(self):
        claim = self.submit()
        with self.assertRaises(WorkflowError):  # no handover before the in-person approval
            custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office,
                                      self.guard, "otp")
        claims.approve_claim(claim.pk, self.guard, DESK["reason"], verified_in_person=True)
        self.assertEqual(Item.objects.get(pk=claim.item_id).status, Item.Status.AWAITING_HANDOVER)
        handover = custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office,
                                             self.guard, "otp")
        custody.email_collection_code(handover.pk, self.guard)
        import re
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        ok, _ = custody.verify_collection_code(handover.pk, self.guard, code)
        self.assertTrue(ok)
        ok, msg = custody.complete_handover(handover.pk, self.guard, id_type="student_id", id_name_matches=True)
        self.assertTrue(ok, msg)
        self.assertEqual(Item.objects.get(pk=claim.item_id).status, Item.Status.RETURNED)
        self.assertEqual(Claim.objects.get(pk=claim.pk).status, Claim.Status.COMPLETED)
