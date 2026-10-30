"""Simplified claim submission: description form, staff review, info requests, no auto-approval."""
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from lostfound.models import AuditEntry, Claim, ClaimMessage, VerificationChallenge
from lostfound.services import claims
from lostfound.services.errors import WorkflowError

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase, upload


class SimpleClaimTests(LFTestCase):
    def submit(self, item=None, user=None):
        return claims.submit_claim(item or self.found_item(), user or self.student, "Black backpack with a sticker",
                                   where_when="Library, Tuesday afternoon", extra_info="Has a blue keyring")

    def test_form_submission_creates_pending_review_claim_without_challenge(self):
        item = self.found_item()
        self.client.force_login(self.student)
        r = self.client.post(reverse("claim_create", args=[item.pk]), {
            "statement": "Black backpack with a laptop sleeve", "where_when": "Reading hall", "lost_when": "Monday morning",
            "extra_info": "", "attachment": upload()})
        claim = Claim.objects.get(item=item, claimant=self.student)
        self.assertRedirects(r, reverse("claim_detail", args=[claim.pk]))
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)
        self.assertEqual((claim.lost_where_when, claim.lost_when), ("Reading hall", "Monday morning"))
        self.assertTrue(claim.attachment)
        self.assertFalse(VerificationChallenge.objects.filter(claim=claim).exists())
        page = self.client.get(reverse("claim_detail", args=[claim.pk]))
        self.assertContains(page, "Under review by campus security")
        self.assertNotContains(page, SECRET_ANSWER)
        self.assertNotContains(page, SECRET_SERIAL)

    def test_form_shows_new_questions_and_button(self):
        self.client.force_login(self.student)
        r = self.client.get(reverse("claim_create", args=[self.found_item().pk]))
        for text in ("What does the item look like?", "Where did you last see or lose it?", "Approximately when did you lose it?",
                     "Any additional information?", "Submit Claim for Review"):
            self.assertContains(r, text)
        self.assertNotContains(r, SECRET_ANSWER)

    def test_never_auto_approved_and_needs_comparison_confirmation(self):
        claim = self.submit()
        self.assertNotEqual(claim.status, Claim.Status.APPROVED)
        with self.assertRaises(WorkflowError):
            claims.approve_claim(claim.pk, self.guard)
        self.client.force_login(self.guard)
        self.client.post(reverse("security_claim_approve", args=[claim.pk]), {"reason": "ok"})
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)
        self.client.post(reverse("security_claim_approve", args=[claim.pk]), {"confirm_compared": "on"})
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.APPROVED)
        self.assertEqual(claim.decided_by, self.guard)
        self.assertTrue(AuditEntry.objects.filter(action="claim.approved").exists())

    def test_members_cannot_approve_or_request_info_via_direct_urls(self):
        claim = self.submit()
        self.client.force_login(self.other)
        for name in ("security_claim_approve", "security_claim_request_info", "security_claim"):
            r = self.client.post(reverse(name, args=[claim.pk]), {"confirm_compared": "on", "message": "hello there"})
            self.assertEqual(r.status_code, 403, name)
        self.client.force_login(self.student)
        r = self.client.post(reverse("security_claim_approve", args=[claim.pk]), {"confirm_compared": "on"})
        self.assertEqual(r.status_code, 403)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)
        with self.assertRaises(PermissionDenied):
            claims.approve_claim(claim.pk, self.student, verified_by_staff=True)

    def test_info_request_round_trip(self):
        claim = self.submit()
        self.client.force_login(self.guard)
        self.client.post(reverse("security_claim_request_info", args=[claim.pk]),
                         {"message": "Can you describe what is inside the front pocket?"})
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.INFO_REQUESTED)
        with self.assertRaises(WorkflowError):  # can't approve while waiting
            claims.approve_claim(claim.pk, self.guard, verified_by_staff=True)
        # another member can't answer someone else's claim
        self.client.force_login(self.other)
        r = self.client.post(reverse("claim_respond", args=[claim.pk]), {"message": "Nope"})
        self.assertEqual(r.status_code, 404)
        self.client.force_login(self.student)
        page = self.client.get(reverse("claim_detail", args=[claim.pk]))
        self.assertContains(page, "front pocket")
        self.client.post(reverse("claim_respond", args=[claim.pk]), {"message": "A charger and two pens."})
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)
        self.assertEqual(ClaimMessage.objects.filter(claim=claim).count(), 2)
        self.assertTrue(AuditEntry.objects.filter(action="claim.info_requested").exists())
        self.assertTrue(AuditEntry.objects.filter(action="claim.info_provided").exists())

    def test_review_page_shows_comparison_material_to_staff(self):
        claim = self.submit()
        self.client.force_login(self.guard)
        r = self.client.get(reverse("security_claim", args=[claim.pk]))
        self.assertContains(r, "Library, Tuesday afternoon")
        self.assertContains(r, "Finder's original report")
        self.assertContains(r, "Black backpack found on a bench.")
        self.assertContains(r, SECRET_ANSWER)
        self.assertTrue(AuditEntry.objects.filter(action="private_detail.viewed").exists())

    def test_claim_attachment_is_private(self):
        item = self.found_item()
        self.client.force_login(self.student)
        self.client.post(reverse("claim_create", args=[item.pk]), {
            "statement": "Black backpack with a laptop sleeve", "where_when": "Reading hall", "lost_when": "Monday",
            "attachment": upload()})
        claim = Claim.objects.get(item=item)
        url = reverse("security_private_file", args=["claim", claim.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.guard)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_rejection_reason_shown_and_handover_requires_approval(self):
        claim = self.submit()
        from django.utils import timezone
        from datetime import timedelta
        from lostfound.services import custody
        with self.assertRaises(WorkflowError):
            custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office,
                                      self.guard, "otp")
        claims.reject_claim(claim.pk, self.guard, "Description doesn't match the item.")
        self.client.force_login(self.student)
        self.assertContains(self.client.get(reverse("claim_detail", args=[claim.pk])), "doesn&#x27;t match")
