from django.urls import reverse

from lostfound.models import Claim

from .base import LFTestCase

SECURITY_GET = ["security_dashboard", "security_items", "security_claims", "security_handovers", "security_custody", "security_ageing"]
ADMIN_GET = ["admin_overview", "admin_analytics", "admin_categories", "admin_locations", "admin_settings", "admin_users", "admin_audit"]


class AccessControlTests(LFTestCase):
    def test_anonymous_redirected_from_protected_pages(self):
        for name in ["dashboard", "my_reports", "my_claims", "notifications"] + SECURITY_GET + ADMIN_GET:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse("login"), r["Location"])

    def test_students_get_403_on_security_and_admin_pages(self):
        self.client.force_login(self.student)
        for name in SECURITY_GET + ADMIN_GET:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_students_cannot_post_security_actions(self):
        claim = self.verified_claim()
        item = claim.item
        self.client.force_login(self.student)
        posts = [("security_claim_approve", claim.pk, {}), ("security_claim_reject", claim.pk, {"reason": "x"}),
                 ("security_item_receive", item.pk, {"location": self.office.pk}),
                 ("security_item_transfer", item.pk, {"location": self.office.pk}),
                 ("security_private_detail_add", item.pk, {}), ("security_handover_schedule", claim.pk, {}),
                 ("security_ageing_dispose", item.pk, {}), ("security_ageing_review", item.pk, {}),
                 ("security_handover_cancel", 1, {}), ("admin_run_matching", None, {})]
        for name, pk, data in posts:
            url = reverse(name, args=[pk]) if pk else reverse(name)
            self.assertEqual(self.client.post(url, data).status_code, 403, name)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.UNDER_REVIEW)

    def test_claim_review_page_requires_private_detail_permission(self):
        claim = self.verified_claim()
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("security_claim", args=[claim.pk])).status_code, 403)
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("security_claim", args=[claim.pk])).status_code, 200)

    def test_users_cannot_see_others_reports_or_claims(self):
        lost = self.lost_item()
        claim = self.verified_claim()
        self.client.force_login(self.other)
        for name, pk in [("report_detail", lost.pk), ("report_matches", lost.pk), ("claim_detail", claim.pk),
                         ("claim_verify", claim.pk)]:
            self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 404, name)
        for name in ["claim_withdraw", "handover_code"]:
            self.assertEqual(self.client.post(reverse(name, args=[claim.pk])).status_code, 404, name)
        self.assertNotContains(self.client.get(reverse("my_claims")), claim.reference)

    def test_security_cannot_manage_users_and_campus_admin_cannot_change_matching(self):
        self.client.force_login(self.guard)
        self.assertEqual(self.client.get(reverse("admin_users")).status_code, 403)
        self.client.force_login(self.cadmin)
        r = self.client.post(reverse("admin_settings"), {"matching": "1", "mat-match_threshold": "0.01"})
        self.assertEqual(r.status_code, 403)

    def test_user_roles_can_be_changed_by_system_admin_only(self):
        url = reverse("admin_user_edit", args=[self.student.pk])
        self.client.force_login(self.cadmin)
        self.assertEqual(self.client.post(url, {"roles": ["Security Personnel"], "is_active": "on"}).status_code, 403)
        self.client.force_login(self.sysadmin)
        self.client.post(url, {"roles": ["Students & Staff", "Security Personnel"], "is_active": "on"})
        self.assertFalse(self.student.groups.filter(name="Security Personnel").exists())  # password required
        self.client.post(url, {"roles": ["Students & Staff", "Security Personnel"], "is_active": "on",
                               "confirm_password": "pw-Long-enough-1"})
        self.assertTrue(self.student.groups.filter(name="Security Personnel").exists())

    def test_system_admin_cannot_lock_themselves_out(self):
        self.client.force_login(self.sysadmin)
        self.client.post(reverse("admin_user_edit", args=[self.sysadmin.pk]), {"roles": [], "is_active": "on"})
        self.assertTrue(self.sysadmin.groups.filter(name="System Administrator").exists())

    def test_private_files_require_permission(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("security_private_file", args=["signature", 1])).status_code, 403)
