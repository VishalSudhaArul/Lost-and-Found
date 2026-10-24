"""Report review, community points, badges, leaderboard privacy, staff sign-in
and administrator account management."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from lostfound import roles
from lostfound.models import (Item, OperationalSettings, PointsLedgerEntry, RewardPolicy, UserBadge,
                              UserRewardProfile)
from lostfound.services import custody, reports, rewards
from lostfound.services.errors import WorkflowError

from .base import LFTestCase, User


def balance(user):
    return sum(PointsLedgerEntry.objects.filter(user=user).values_list("points", flat=True))


class ReportReviewTests(LFTestCase):
    def test_new_reports_await_review_and_verification_awards_once(self):
        item = self.found_item(receive=False)
        self.assertEqual(item.review_status, Item.Review.PENDING)
        reports.verify(item.pk, self.guard)
        reports.verify(item.pk, self.guard)  # repeat is harmless
        self.assertEqual(PointsLedgerEntry.objects.filter(user=self.finder, category="report").count(), 1)
        self.assertEqual(balance(self.finder), RewardPolicy.load().points_found_report)

    def test_rejected_report_is_hidden_and_earns_nothing(self):
        item = self.found_item(receive=False)
        reports.reject(item.pk, self.guard, "Duplicate of an earlier report")
        item.refresh_from_db()
        self.assertTrue(item.is_hidden)
        self.assertFalse(Item.objects.publicly_listed().filter(pk=item.pk).exists())
        self.assertEqual(balance(self.finder), 0)
        self.assertIsNone(rewards.on_found_report_verified(item, self.guard))

    def test_members_cannot_review_and_reviewers_cannot_review_own_report(self):
        item = self.found_item(receive=False)
        with self.assertRaises(PermissionDenied):
            reports.verify(item.pk, self.student)
        own = self.found_item(reporter=self.guard, receive=False, title="Guard's own find")
        with self.assertRaises(WorkflowError):
            reports.verify(own.pk, self.guard)
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(reverse("security_report_verify", args=[item.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("security_reports")).status_code, 403)

    def test_hold_setting_keeps_unverified_reports_off_the_board(self):
        item = self.found_item(receive=False)
        cfg = OperationalSettings.load()
        cfg.hold_reports_for_review = True
        cfg.save()
        self.assertFalse(Item.objects.publicly_listed().filter(pk=item.pk).exists())
        reports.verify(item.pk, self.guard)
        self.assertTrue(Item.objects.publicly_listed().filter(pk=item.pk).exists())

    def test_custody_receipt_verifies_and_awards_custody_points(self):
        item = self.found_item()  # fixture receives it into custody
        item.refresh_from_db()
        self.assertEqual(item.review_status, Item.Review.VERIFIED)
        cats = set(PointsLedgerEntry.objects.filter(user=self.finder).values_list("category", flat=True))
        self.assertEqual(cats, {"report", "custody"})

    def test_review_queue_and_page_render_for_the_team(self):
        item = self.found_item(receive=False)
        self.client.force_login(self.guard)
        self.assertContains(self.client.get(reverse("security_reports")), item.reference)
        self.assertContains(self.client.get(reverse("security_report", args=[item.pk])), "Verify report")
        r = self.client.post(reverse("security_report_reject", args=[item.pk]), {"reason": "Not a campus item"})
        self.assertRedirects(r, reverse("security_reports"))


class PointsTests(LFTestCase):
    def test_return_points_only_after_confirmed_handover_and_only_once(self):
        handover = self.scheduled_handover()
        self.assertFalse(PointsLedgerEntry.objects.filter(category="return").exists())
        handover.status, handover.completed_by = handover.Status.COMPLETED, self.guard
        rewards.on_handover_completed(handover)
        rewards.on_handover_completed(handover)
        self.assertEqual(PointsLedgerEntry.objects.filter(user=self.finder, category="return").count(), 1)

    def test_no_return_points_for_returning_to_yourself(self):
        # Claims already block finders from claiming their own item; the rewards
        # service refuses self-dealing as a second, independent guard.
        handover = self.scheduled_handover()
        Item.objects.filter(pk=handover.claim.item_id).update(reporter=handover.claim.claimant)
        handover.claim.item.refresh_from_db()
        handover.completed_by = self.guard
        self.assertIsNone(rewards.on_handover_completed(handover))

    def test_ledger_is_append_only_and_reversal_is_single(self):
        item = self.found_item()
        entry = PointsLedgerEntry.objects.filter(user=self.finder, category="report").get()
        entry.points = 999
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(PermissionDenied):
            rewards.reverse_entry(entry.pk, self.student, "Trying to change my balance")
        rewards.reverse_entry(entry.pk, self.cadmin, "Report turned out to be a duplicate")
        with self.assertRaises(WorkflowError):
            rewards.reverse_entry(entry.pk, self.cadmin, "Second attempt")
        self.assertEqual(balance(self.finder), RewardPolicy.load().points_custody)
        self.assertEqual(item.pk, entry.item_id)

    def test_community_awards_are_bounded_and_not_self_awarded(self):
        with self.assertRaises(WorkflowError):
            rewards.award_community(self.cadmin, 5, "Helping myself", self.cadmin)
        with self.assertRaises(WorkflowError):
            rewards.award_community(self.student, 10_000, "Too many points", self.cadmin)
        with self.assertRaises(PermissionDenied):
            rewards.award_community(self.student, 5, "Not allowed", self.guard)
        rewards.award_community(self.student, 20, "Ran the awareness stall", self.cadmin)
        self.assertEqual(balance(self.student), 20)

    def test_members_cannot_reach_reward_admin_or_edit_points(self):
        self.client.force_login(self.student)
        for name in ("admin_rewards", "admin_rewards_ledger"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin_rewards"), {"action": "award", "aw-username": "student",
                                                                     "aw-points": 500, "aw-reason": "free points"}).status_code, 403)
        self.assertEqual(balance(self.student), 0)

    def test_disabled_rewards_award_nothing(self):
        cfg = RewardPolicy.load()
        cfg.rewards_enabled = False
        cfg.save()
        self.found_item()
        self.assertFalse(PointsLedgerEntry.objects.exists())


class BadgeTests(LFTestCase):
    def test_badges_awarded_once_and_revoked_after_correction(self):
        self.found_item()  # report + custody = 2 verified actions
        codes = set(UserBadge.objects.filter(user=self.finder).values_list("badge__code", flat=True))
        self.assertIn("helpful-neighbor", codes)
        self.assertIn("first-found-report", codes)
        rewards.evaluate_badges(self.finder)
        self.assertEqual(UserBadge.objects.filter(user=self.finder, badge__code="helpful-neighbor").count(), 1)
        report = PointsLedgerEntry.objects.get(user=self.finder, category="report")
        rewards.reverse_entry(report.pk, self.cadmin, "Report was a duplicate")
        self.assertFalse(UserBadge.objects.filter(user=self.finder, badge__code="first-found-report").exists())

    def test_summary_shows_progress_to_next_tier(self):
        self.found_item()
        s = rewards.summary(self.finder)
        self.assertEqual(s["current_tier"].code, "helpful-neighbor")
        self.assertEqual(s["progress"]["tier"].code, "campus-helper")
        self.assertEqual(s["progress"]["remaining"], 3)


class LeaderboardTests(LFTestCase):
    def test_opt_out_hides_member_and_names_are_safe(self):
        self.found_item()
        rows = rewards.leaderboard()
        self.assertEqual(rows[0]["name"], "Finder")
        self.assertNotIn("@", str(rows))
        UserRewardProfile.objects.update_or_create(user=self.finder, defaults={"show_on_leaderboard": False})
        self.assertEqual(rewards.leaderboard(), [])

    def test_leaderboard_page_requires_sign_in_and_hides_emails(self):
        self.found_item()
        self.assertEqual(self.client.get(reverse("leaderboard")).status_code, 302)
        self.client.force_login(self.student)
        r = self.client.get(reverse("leaderboard"))
        self.assertContains(r, "Finder")
        self.assertNotContains(r, "finder@uni.test")

    def test_profile_updates_nickname_and_visibility(self):
        self.client.force_login(self.finder)
        r = self.client.post(reverse("profile"), {"first_name": "Finder", "display_name": "Bottle Hero",
                                                  "show_on_leaderboard": "on"})
        self.assertRedirects(r, reverse("profile"))
        self.found_item()
        self.assertEqual(rewards.leaderboard()[0]["name"], "Bottle Hero")

    def test_rewards_page_public_and_personal(self):
        self.assertContains(self.client.get(reverse("rewards")), "Good deeds deserve recognition")
        self.found_item()
        self.client.force_login(self.finder)
        r = self.client.get(reverse("rewards"))
        self.assertContains(r, "Available points")
        self.assertEqual(r.context["me"]["balance"], 15)


class StaffAndAccountTests(LFTestCase):
    def test_staff_sign_in_refuses_member_accounts(self):
        r = self.client.post(reverse("staff_login"), {"username": "student", "password": "pw-Long-enough-1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Lost &amp; Found team and administrators")
        self.assertFalse("_auth_user_id" in self.client.session)

    def test_admin_creates_team_account_and_edits_details(self):
        from django.core import mail
        self.client.force_login(self.sysadmin)
        data = {"first_name": "Nila", "last_name": "R", "email": "nila@uni.test", "member_type": "staff",
                "department": "Security", "username": "nila", "roles": [roles.SECURITY]}
        r = self.client.post(reverse("admin_user_create"), {**data, "confirm_password": "wrong"})
        self.assertFalse(User.objects.filter(username="nila").exists())
        r = self.client.post(reverse("admin_user_create"), {**data, "confirm_password": "pw-Long-enough-1"})
        user = User.objects.get(username="nila")
        self.assertRedirects(r, reverse("admin_user_edit", args=[user.pk]))
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertIn("/accounts/activate/", mail.outbox[-1].body)
        self.assertTrue(user.groups.filter(name=roles.SECURITY).exists())
        self.assertTrue(user.groups.filter(name=roles.MEMBERS).exists())
        r = self.client.post(reverse("admin_user_edit", args=[user.pk]), {
            "action": "details", "first_name": "Nila", "last_name": "Raman", "email": "nila.r@uni.test",
            "member_type": "staff", "department": "Campus Security"})
        user.refresh_from_db()
        self.assertEqual((user.last_name, user.email), ("Raman", "nila.r@uni.test"))

    def test_account_management_is_admin_only_and_tabs_filter(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("admin_user_create")).status_code, 403)
        self.client.force_login(self.sysadmin)
        r = self.client.get(reverse("admin_users"), {"tab": "team"})
        names = {u.username for u in r.context["page"]}
        self.assertEqual(names, {"guard", "guard2"})

    def test_admin_can_deactivate_and_send_reset(self):
        from django.core import mail
        self.client.force_login(self.sysadmin)
        self.client.post(reverse("admin_user_edit", args=[self.other.pk]), {"action": "reset"})
        self.assertEqual(len(mail.outbox), 1)
        self.client.post(reverse("admin_user_edit", args=[self.other.pk]),
                         {"action": "roles", "roles": [roles.MEMBERS]})
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_active)
        self.assertFalse(self.client.login(username="other", password="pw-Long-enough-1"))
