from django.core.management import call_command
from django.urls import reverse

from lostfound.services import analytics, custody

from .base import LFTestCase


class DashboardAndMiscTests(LFTestCase):
    def test_metrics_on_empty_database(self):
        m = analytics.admin_metrics()
        self.assertIsNone(m["return_rate"])
        self.assertIsNone(m["median_hours_to_return"])
        self.client.force_login(self.cadmin)
        self.assertEqual(self.client.get(reverse("admin_analytics")).status_code, 200)

    def test_metrics_reflect_records(self):
        self.lost_item()
        handover = self.scheduled_handover()
        self.found_item(title="Second")
        code, _ = custody.issue_otp(handover.pk, self.student)
        custody.complete_handover(handover.pk, self.guard, id_type="student_id", id_name_matches=True, otp_code=code)
        m = analytics.admin_metrics()
        self.assertEqual((m["lost_total"], m["found_total"], m["returned"]), (1, 2, 1))
        self.assertEqual(m["return_rate"], 50.0)
        self.assertIsNotNone(m["median_hours_to_return"])
        self.assertEqual(m["verification_failure_rate"], 0.0)
        s = analytics.security_metrics()
        self.assertEqual(s["in_custody"], 1)
        mm = analytics.member_metrics(self.student)
        self.assertEqual(mm["pending_claims"], 0)

    def test_dashboard_pages_render_for_each_role(self):
        self.verified_claim()
        for user, names in [(self.student, ["dashboard", "board", "my_reports", "my_claims", "notifications"]),
                            (self.guard, ["security_dashboard", "security_items", "security_claims", "security_handovers",
                                          "security_custody", "security_ageing"]),
                            (self.cadmin, ["admin_overview", "admin_analytics", "admin_categories", "admin_locations", "admin_settings"]),
                            (self.sysadmin, ["admin_users", "admin_audit", "admin_settings"])]:
            self.client.force_login(user)
            for name in names:
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, f"{user} {name}")

    def test_login_rate_limit(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "student", "password": "wrong"})
        r = self.client.post(reverse("login"), {"username": "student", "password": "pw-Long-enough-1"})
        self.assertContains(r, "Too many failed sign-in attempts")

    def test_registration_assigns_member_role_and_respects_domain(self):
        data = {"username": "newbie", "first_name": "New", "last_name": "", "email": "new@gmail.com",
                "member_type": "student", "department": "", "password1": "a-Strong-pass-99", "password2": "a-Strong-pass-99"}
        with self.settings(ALLOWED_EMAIL_DOMAINS=["uni.test"]):
            r = self.client.post(reverse("register"), data)
            self.assertContains(r, "university email")
            data["email"] = "new@uni.test"
            self.client.post(reverse("register"), data)
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.get(username="newbie")
        self.assertTrue(u.groups.filter(name="Students & Staff").exists())
        self.assertFalse(u.has_perm("lostfound.access_security_dashboard"))

    def test_management_commands_run(self):
        self.found_item()
        call_command("refresh_matches", verbosity=0)
        call_command("check_ageing", verbosity=0)
        call_command("check", verbosity=0)

    def test_settings_validation(self):
        self.client.force_login(self.sysadmin)
        r = self.client.post(reverse("admin_settings"), {"matching": "1", "mat-match_threshold": "2"})
        self.assertEqual(r.status_code, 200)
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.match_threshold, 0.45)

    def test_error_pages_render(self):
        self.assertEqual(self.client.get("/does-not-exist/").status_code, 404)
