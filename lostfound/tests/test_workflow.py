"""End-to-end claim → review → handover → finder reward, plus the demo-data purge."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from lostfound import roles
from lostfound.models import (AuditEntry, Claim, Item, Notification, PointsLedgerEntry, RewardPolicy,
                              VerificationChallenge)
from lostfound.services import claims, custody, verification
from lostfound.services.errors import WorkflowError

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase, User


class WorkflowTests(LFTestCase):
    def claim(self, item=None, user=None):
        return claims.submit_claim(item or self.found_item(), user or self.student, "Black backpack, blue keyring",
                                   where_when="Library", lost_when="Tuesday afternoon")

    def approve(self, claim):
        claims.approve_claim(claim.pk, self.guard, "Matches", verified_by_staff=True)
        claim.refresh_from_db()
        return claim

    def schedule(self, claim):
        return custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office,
                                         self.guard, "otp")

    def complete(self, handover, actor=None):
        code, _ = custody.issue_otp(handover.pk, handover.claim.claimant)
        return custody.complete_handover(handover.pk, actor or self.guard, id_type="student_id",
                                         id_name_matches=True, otp_code=code)

    def return_points(self, user):
        return list(PointsLedgerEntry.objects.filter(user=user, category="return").values_list("points", flat=True))

    def test_simple_statuses_follow_the_workflow(self):
        claim = self.claim()
        self.assertEqual(claim.display_status, "Under review")
        claims.request_info(claim.pk, self.guard, "Any marks on the strap?")
        claim.refresh_from_db()
        self.assertEqual(claim.display_status, "More information requested")
        claims.respond_to_request(claim.pk, self.student, "A small tear near the buckle.")
        claim = self.approve(Claim.objects.get(pk=claim.pk))
        self.assertEqual(claim.display_status, "Approved")
        self.schedule(claim)
        self.assertEqual(Claim.objects.get(pk=claim.pk).display_status, "Awaiting collection")

    def test_failed_legacy_quiz_does_not_block_manual_review(self):
        item = self.found_item()
        claim = claims.submit_claim(item, self.student, "Black backpack from the library", with_challenge=True)
        wrong = {k: "no idea" for k in verification.questions_for_claimant(claim.challenge) and
                 [str(q["id"]) for q in verification.questions_for_claimant(claim.challenge)]}
        for _ in range(claim.challenge.max_attempts):
            try:
                verification.submit_attempt(claim.challenge.pk, self.student, wrong)
            except WorkflowError:
                break
        claim.refresh_from_db()
        self.assertEqual(claim.verification_status, Claim.Verification.FAILED)
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)
        # claimant never sees answers or which one was wrong
        self.client.force_login(self.student)
        page = self.client.get(reverse("claim_detail", args=[claim.pk])).content.decode()
        self.assertNotIn(SECRET_ANSWER, page)
        self.assertNotIn(SECRET_SERIAL, page)
        # staff can still request info and approve manually; the override is audited
        claims.request_info(claim.pk, self.guard, "Describe the keyring please.")
        claims.respond_to_request(claim.pk, self.student, "Blue rubber football keyring.")
        self.approve(claim)
        entry = AuditEntry.objects.filter(action="claim.approved").latest("pk")
        self.assertTrue(entry.metadata["legacy_quiz_override"])
        self.assertEqual(entry.metadata["legacy_quiz_result"], "failed")

    def test_open_legacy_quiz_can_be_approved_manually(self):
        claim = claims.submit_claim(self.found_item(), self.student, "Black backpack from the library", with_challenge=True)
        self.approve(claim)
        self.assertEqual(claim.status, Claim.Status.APPROVED)
        self.assertEqual(VerificationChallenge.objects.get(claim=claim).status, VerificationChallenge.Status.CANCELLED)

    def test_approval_keeps_item_in_custody_until_handover(self):
        claim = self.approve(self.claim())
        item = Item.objects.get(pk=claim.item_id)
        self.assertEqual(item.status, Item.Status.AWAITING_HANDOVER)
        self.assertIsNotNone(item.current_custody_location)
        self.assertEqual(self.return_points(self.finder), [])
        handover = self.schedule(claim)
        self.assertEqual(Item.objects.get(pk=item.pk).status, Item.Status.AWAITING_HANDOVER)
        ok, _ = self.complete(handover)
        self.assertTrue(ok)
        item.refresh_from_db()
        self.assertEqual(item.status, Item.Status.RETURNED)
        self.assertIsNone(item.current_custody_location)
        self.assertEqual(Claim.objects.get(pk=claim.pk).display_status, "Returned")

    def test_finder_gets_report_reward_and_return_bonus_exactly_once(self):
        item = self.found_item()  # received into custody → report auto-verified
        self.assertEqual(list(PointsLedgerEntry.objects.filter(user=self.finder, category="report")
                              .values_list("points", flat=True)), [RewardPolicy.load().points_found_report])
        handover = self.schedule(self.approve(self.claim(item)))
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.complete(handover)
        self.assertEqual(self.return_points(self.finder), [40])
        self.assertEqual(self.return_points(self.student), [])
        with self.assertRaises(WorkflowError):  # second completion is refused
            self.complete(handover)
        from lostfound.services import rewards
        rewards.on_handover_completed(handover)  # replayed hook awards nothing
        self.assertEqual(self.return_points(self.finder), [40])
        self.assertTrue(Notification.objects.filter(user=self.finder, kind="reward").exists())
        to = [m.to for m in mail.outbox]
        self.assertIn(["finder@uni.test"], to)   # return + reward emails to the finder
        self.assertIn(["student@uni.test"], to)  # "item returned" to the owner

    def test_return_bonus_is_configurable(self):
        policy = RewardPolicy.load()
        policy.points_return = 55
        policy.save()
        self.complete(self.schedule(self.approve(self.claim())))
        self.assertEqual(self.return_points(self.finder), [55])

    def test_no_points_for_rejected_or_cancelled(self):
        claim = self.claim()
        claims.reject_claim(claim.pk, self.guard, "Doesn't match.")
        other = self.approve(self.claim(user=self.other))
        handover = self.schedule(other)
        custody.cancel_handover(handover.pk, self.guard, "Claimant unavailable")
        self.assertEqual(self.return_points(self.finder), [])
        self.assertNotEqual(Item.objects.get(pk=other.item_id).status, Item.Status.RETURNED)

    def test_no_reward_for_lost_reports(self):
        lost = self.lost_item()
        self.assertFalse(PointsLedgerEntry.objects.filter(item=lost).exists())

    def test_unauthorised_users_cannot_approve_or_release(self):
        claim = self.claim()
        with self.assertRaises(PermissionDenied):
            claims.approve_claim(claim.pk, self.other, verified_by_staff=True)
        claim = self.approve(claim)
        handover = self.schedule(claim)
        code, _ = custody.issue_otp(handover.pk, self.student)
        for actor in (self.other, self.student, self.finder):
            with self.assertRaises(PermissionDenied):
                custody.complete_handover(handover.pk, actor, id_type="student_id", id_name_matches=True, otp_code=code)
        self.client.force_login(self.other)
        r = self.client.post(reverse("security_handover", args=[handover.pk]),
                             {"id_type": "student_id", "id_name_matches": "on", "otp_code": code})
        self.assertEqual(r.status_code, 403)
        self.assertNotEqual(Item.objects.get(pk=claim.item_id).status, Item.Status.RETURNED)
        self.assertEqual(self.return_points(self.finder), [])

    def test_custody_page_stages_and_next_step(self):
        claimed = self.found_item(title="Claimed bag")
        idle = self.found_item(title="Idle bottle")
        self.claim(claimed)
        self.client.force_login(self.guard)
        r = self.client.get(reverse("security_items") + "?state=claims")
        self.assertContains(r, "Claimed bag")
        self.assertNotContains(r, "Idle bottle")
        r = self.client.get(reverse("security_items") + "?state=custody")
        self.assertContains(r, "Idle bottle")
        self.assertNotContains(r, "Claimed bag")
        r = self.client.get(reverse("security_item", args=[claimed.pk]))
        self.assertContains(r, "Next step")
        self.assertContains(r, "waiting for review")
        self.assertNotContains(r, SECRET_ANSWER)

    def test_success_message_and_handover_button(self):
        item = self.found_item()
        self.client.force_login(self.student)
        r = self.client.post(reverse("claim_create", args=[item.pk]), {
            "statement": "Black backpack", "where_when": "Library", "lost_when": "Monday"}, follow=True)
        self.assertContains(r, "Your claim has been submitted successfully. Campus security will review your "
                               "information and contact you.")
        handover = self.schedule(self.approve(Claim.objects.get(item=item)))
        self.client.force_login(self.guard)
        self.assertContains(self.client.get(reverse("security_handover", args=[handover.pk])), "Complete Handover")


class PurgeDemoDataTests(LFTestCase):
    def make_seed(self):
        seeded = []
        for name in ("student1", "security1"):
            u = User.objects.create_user(name, f"{name}@example.edu", "x", last_name="(sample)")
            u.groups.add(Group.objects.get(name=roles.MEMBERS))
            seeded.append(u)
        item = self.found_item(title="[Sample] Bottle", reporter=seeded[0], receive=False)
        return seeded, item

    def test_dry_run_deletes_nothing_and_apply_removes_only_seeded_rows(self):
        seeded, item = self.make_seed()
        lookalike = User.objects.create_user("student2", "real.person@uni.test", "x", last_name="(sample)")
        mine = self.found_item(title="[Sample] my own joke title")  # real user's item, sample-like name
        call_command("purge_demo_data", stdout=StringIO())
        self.assertTrue(User.objects.filter(pk=seeded[0].pk).exists())
        call_command("purge_demo_data", "--apply", stdout=StringIO())
        self.assertFalse(User.objects.filter(pk__in=[u.pk for u in seeded]).exists())
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())
        self.assertTrue(User.objects.filter(pk=lookalike.pk).exists())
        self.assertTrue(Item.objects.filter(pk=mine.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())

    def test_stops_when_real_records_depend_on_seeded_ones(self):
        seeded, item = self.make_seed()
        custody.receive_item(item.pk, self.guard, self.office)
        claims.submit_claim(Item.objects.get(pk=item.pk), self.student, "That bottle is mine, really.")
        with self.assertRaises(CommandError):
            call_command("purge_demo_data", "--apply", stdout=StringIO())
        self.assertTrue(User.objects.filter(pk=seeded[0].pk).exists())
