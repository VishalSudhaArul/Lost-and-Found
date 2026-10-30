from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction

from lostfound.models import Claim, Item
from lostfound.services import claims
from lostfound.services.errors import WorkflowError

from .base import LFTestCase


class ClaimWorkflowTests(LFTestCase):
    def test_duplicate_active_claim_prevented(self):
        item = self.found_item()
        claims.submit_claim(item, self.student, "Mine, lost it near the reading hall windows.")
        with self.assertRaisesMessage(WorkflowError, "already have an active claim"):
            claims.submit_claim(item, self.student, "Mine again, lost it near the windows.")

    def test_finder_cannot_claim_own_found_item(self):
        item = self.found_item()
        with self.assertRaises(WorkflowError):
            claims.submit_claim(item, self.finder, "I want to claim the thing I found myself.")

    def test_cannot_link_someone_elses_lost_report(self):
        item = self.found_item()
        other_lost = self.lost_item(reporter=self.other)
        with self.assertRaises(PermissionDenied):
            claims.submit_claim(item, self.student, "Linking another person's lost report.", lost_report=other_lost)

    def test_claim_rate_limit(self):
        with self.settings(CLAIM_RATE_LIMIT=(1, 3600)):
            claims.submit_claim(self.found_item(title="A"), self.student, "First claim of the hour, a backpack.")
            with self.assertRaisesMessage(WorkflowError, "too many claims"):
                claims.submit_claim(self.found_item(title="B"), self.student, "Second claim of the hour, a backpack.")

    def test_competing_claims_need_explicit_confirmation_and_are_rejected(self):
        item = self.found_item()
        a = self.verified_claim(item=item, claimant=self.student)
        b = self.verified_claim(item=item, claimant=self.other)
        pending = claims.submit_claim(item, self.third, "I think this backpack belongs to me as well.")
        with self.assertRaisesMessage(WorkflowError, "competing"):
            claims.approve_claim(a.pk, self.guard, reason="Verified at the desk", verified_in_person=True)
        claims.approve_claim(a.pk, self.guard, confirm_competing=True, reason="Verified at the desk", verified_in_person=True)
        for c in (b, pending):
            c.refresh_from_db()
            self.assertEqual(c.status, Claim.Status.REJECTED)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.Status.AWAITING_HANDOVER)
        with self.assertRaises(WorkflowError):
            claims.approve_claim(b.pk, self.guard2, confirm_competing=True, reason="Verified at the desk", verified_in_person=True)

    def test_database_rejects_two_approved_claims_for_one_item(self):
        item = self.found_item()
        a = self.verified_claim(item=item, claimant=self.student)
        b = self.verified_claim(item=item, claimant=self.other)
        Claim.objects.filter(pk=a.pk).update(status=Claim.Status.APPROVED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Claim.objects.filter(pk=b.pk).update(status=Claim.Status.APPROVED)

    def test_claimant_cannot_decide_own_claim(self):
        guard_claim = self.verified_claim(claimant=self.guard)
        with self.assertRaises(PermissionDenied):
            claims.approve_claim(guard_claim.pk, self.guard)
        with self.assertRaises(PermissionDenied):
            claims.approve_claim(guard_claim.pk, self.student)  # no permission at all

    def test_approval_requires_custody(self):
        item = self.found_item(receive=False)
        claim = self.verified_claim(item=item)
        with self.assertRaisesMessage(WorkflowError, "custody"):
            claims.approve_claim(claim.pk, self.guard, reason="Verified at the desk", verified_in_person=True)

    def test_rejection_needs_reason_and_is_visible_to_claimant(self):
        claim = self.verified_claim()
        with self.assertRaises(WorkflowError):
            claims.reject_claim(claim.pk, self.guard, "  ")
        claims.reject_claim(claim.pk, self.guard, "Serial number did not match the device.")
        self.client.force_login(self.student)
        r = self.client.get(f"/my/claims/{claim.pk}/")
        self.assertContains(r, "Serial number did not match")

    def test_rejecting_approved_claim_returns_item_to_custody(self):
        claim = self.approved_claim()
        claims.reject_claim(claim.pk, self.guard, "Owner identity could not be confirmed.")
        claim.item.refresh_from_db()
        self.assertEqual(claim.item.status, Item.Status.IN_CUSTODY)

    def test_withdraw(self):
        claim = self.verified_claim()
        with self.assertRaises(PermissionDenied):
            claims.withdraw_claim(claim.pk, self.other)
        claims.withdraw_claim(claim.pk, self.student)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.WITHDRAWN)
