"""Student/teacher portals, two-step staff sign-in, email activation of
administrator-created accounts, role escalation checks and admin overview counts."""
import re

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from lostfound import roles
from lostfound.models import Item, UserProfile

from .base import LFTestCase, User

PW = "pw-Long-enough-1"


def code_from_mail():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class PortalTests(LFTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        UserProfile.objects.create(user=cls.student, member_type=UserProfile.MemberType.STUDENT)
        cls.teacher = cls.make_user("teacher", roles.MEMBERS)
        UserProfile.objects.create(user=cls.teacher, member_type=UserProfile.MemberType.STAFF)

    def test_each_portal_only_accepts_its_own_accounts(self):
        r = self.client.post(reverse("teacher_login"), {"username": "student", "password": PW})
        self.assertContains(r, "This is the teacher sign-in")
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("student_login"), {"username": "student", "password": PW})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)
        self.client.logout()
        r = self.client.post(reverse("student_login"), {"username": "teacher", "password": PW})
        self.assertContains(r, "This is the student sign-in")
        r = self.client.post(reverse("teacher_login"), {"username": "teacher", "password": PW})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)

    def test_portal_registration_sets_the_account_type(self):
        data = {"username": "newteacher", "first_name": "Kavya", "email": "kavya@uni.test",
                "password1": "a-Strong-pass-99", "password2": "a-Strong-pass-99", "member_type": "student"}
        self.client.post(reverse("teacher_register"), data)  # member_type in the POST is ignored
        self.assertEqual(User.objects.get(username="newteacher").profile.member_type, UserProfile.MemberType.STAFF)

    def test_sign_in_and_register_pages_offer_the_portals(self):
        r = self.client.get(reverse("login"))
        for name in ("student_login", "teacher_login", "staff_login"):
            self.assertContains(r, reverse(name))
        r = self.client.get(reverse("register"))
        self.assertContains(r, reverse("student_register"))
        self.assertContains(r, reverse("teacher_register"))


class StaffTwoStepTests(LFTestCase):
    def test_password_alone_does_not_sign_staff_in(self):
        r = self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        self.assertRedirects(r, reverse("staff_login_verify"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(mail.outbox[-1].to, ["guard@uni.test"])
        code = code_from_mail()
        self.assertNotIn(code, str(self.client.session.get("staff_2fa")))  # only a digest is stored
        self.assertEqual(self.client.get(reverse("security_dashboard")).status_code, 302)
        r = self.client.post(reverse("staff_login_verify"), {"code": code})
        self.assertRedirects(r, reverse("security_dashboard"))
        self.assertEqual(self.client.get(reverse("security_dashboard")).status_code, 200)

    def test_admin_lands_on_admin_overview(self):
        self.client.post(reverse("staff_login"), {"username": "sysadmin", "password": PW})
        r = self.client.post(reverse("staff_login_verify"), {"code": code_from_mail()})
        self.assertRedirects(r, reverse("admin_overview"))

    @override_settings(STAFF_CODE_MAX_ATTEMPTS=3)
    def test_wrong_codes_are_limited_and_code_is_single_use(self):
        self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        code = code_from_mail()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(3):
            r = self.client.post(reverse("staff_login_verify"), {"code": wrong})
        self.assertRedirects(r, reverse("staff_login"))
        r = self.client.post(reverse("staff_login_verify"), {"code": code})
        self.assertRedirects(r, reverse("staff_login"))  # the session was cleared
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(STAFF_CODE_TTL_SECONDS=-1)
    def test_expired_code_is_refused(self):
        self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        r = self.client.post(reverse("staff_login_verify"), {"code": code_from_mail()})
        self.assertRedirects(r, reverse("staff_login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_django_admin_uses_staff_sign_in(self):
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 302)
        r = self.client.get(r["Location"])
        self.assertIn(reverse("staff_login"), r["Location"])


class ActivationTests(LFTestCase):
    def create(self, **extra):
        self.client.force_login(self.sysadmin)
        data = {"first_name": "Arun", "email": "arun@uni.test", "member_type": "staff", "department": "",
                "username": "arun", "roles": [roles.CAMPUS_ADMIN], "confirm_password": PW, **extra}
        return self.client.post(reverse("admin_user_create"), data)

    def test_new_admin_activates_from_their_own_email(self):
        self.create()
        user = User.objects.get(username="arun")
        self.assertFalse(self.client.login(username="arun", password="anything"))
        link = re.search(r"https?://[^/]+(/accounts/activate/\S+/)", mail.outbox[-1].body).group(1)
        self.client.logout()
        r = self.client.post(link, {"new_password1": "Own-secret-2026!", "new_password2": "Own-secret-2026!"})
        self.assertRedirects(r, reverse("staff_login"))
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        r = self.client.post(link, {"new_password1": "Again-2026!x", "new_password2": "Again-2026!x"})
        self.assertEqual(r.status_code, 400)  # link works once

    @override_settings(ALLOWED_STAFF_EMAIL_DOMAINS=["campus.edu"])
    def test_staff_accounts_require_official_email_domain(self):
        r = self.create()
        self.assertContains(r, "official campus email")
        self.assertFalse(User.objects.filter(username="arun").exists())
        self.create(email="arun@campus.edu")
        self.assertTrue(User.objects.filter(username="arun").exists())

    def test_only_user_managers_can_create_accounts(self):
        for user in (self.student, self.guard, self.cadmin):
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("admin_user_create"), {}).status_code, 403)

    def test_granting_admin_role_needs_password(self):
        self.client.force_login(self.sysadmin)
        url = reverse("admin_user_edit", args=[self.student.pk])
        self.client.post(url, {"action": "roles", "roles": [roles.MEMBERS, roles.SYSTEM_ADMIN], "is_active": "on"})
        self.assertFalse(self.student.groups.filter(name=roles.SYSTEM_ADMIN).exists())
        self.client.post(url, {"action": "roles", "roles": [roles.MEMBERS, roles.SYSTEM_ADMIN], "is_active": "on",
                               "confirm_password": PW})
        self.assertTrue(self.student.groups.filter(name=roles.SYSTEM_ADMIN).exists())


class AccessPolicyTests(LFTestCase):
    def save_policy(self, user, **fields):
        self.client.force_login(user)
        data = {"access": "1", "acc-allow_student_registration": "on", "acc-allow_teacher_registration": "on",
                "acc-registration_email_domains": "", "acc-staff_email_domains": "", "acc-staff_email_allowlist": "",
                "acc-confirm_password": PW}
        data.update({f"acc-{k}": v for k, v in fields.items()})
        return self.client.post(reverse("admin_settings"), data)

    def test_admin_restricts_staff_emails_and_sign_in_follows(self):
        r = self.save_policy(self.sysadmin, staff_email_domains="campus.edu", staff_email_allowlist="sysadmin@uni.test")
        self.assertRedirects(r, reverse("admin_settings"))
        self.client.logout()
        r = self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        self.assertContains(r, "isn&#x27;t on the list allowed for staff")
        r = self.client.post(reverse("staff_login"), {"username": "sysadmin", "password": PW})
        self.assertRedirects(r, reverse("staff_login_verify"))

    def test_policy_cannot_lock_out_its_author_and_needs_password(self):
        r = self.save_policy(self.sysadmin, staff_email_domains="campus.edu")
        self.assertContains(r, "would block your own email")
        r = self.save_policy(self.sysadmin, confirm_password="wrong", allow_student_registration="")
        self.assertContains(r, "Your password is incorrect")
        from lostfound.models import OperationalSettings
        self.assertTrue(OperationalSettings.load().allow_student_registration)

    def test_registration_can_be_closed_and_limited_to_domains(self):
        self.save_policy(self.sysadmin, allow_teacher_registration="", registration_email_domains="uni.test")
        self.client.logout()
        self.assertContains(self.client.get(reverse("teacher_register")), "Teacher registration is closed")
        data = {"username": "s9", "first_name": "S", "email": "s9@gmail.com",
                "password1": "a-Strong-pass-99", "password2": "a-Strong-pass-99"}
        self.assertContains(self.client.post(reverse("student_register"), data), "university email")
        self.client.post(reverse("student_register"), {**data, "email": "s9@uni.test"})
        self.assertTrue(User.objects.filter(username="s9").exists())
        self.client.logout()
        self.client.post(reverse("teacher_register"), {**data, "username": "t9", "email": "t9@uni.test"})
        self.assertFalse(User.objects.filter(username="t9").exists())

    def test_only_user_managers_change_policy_and_send_test_email(self):
        self.assertEqual(self.save_policy(self.cadmin).status_code, 403)
        self.client.force_login(self.sysadmin)
        r = self.client.post(reverse("admin_test_email"))
        self.assertRedirects(r, reverse("admin_settings"))
        self.assertEqual(mail.outbox[-1].to, ["sysadmin@uni.test"])
        self.client.force_login(self.student)
        self.assertEqual(self.client.post(reverse("admin_test_email")).status_code, 403)

    def test_email_failure_never_signs_staff_in(self):
        import smtplib
        from unittest import mock
        with mock.patch("lostfound.services.mailer.send_mail", side_effect=OSError("smtp down")):
            r = self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        self.assertContains(r, "couldn&#x27;t send your sign-in code")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("staff_2fa", self.client.session)
        # A rejected login is logged with its SMTP code, but never the address, code or password.
        err = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        with mock.patch("lostfound.services.mailer.send_mail", side_effect=err), \
                self.assertLogs("lostfound.email", level="ERROR") as logs:
            self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        text = " ".join(logs.output)
        self.assertIn("535", text)
        self.assertIn("staff sign-in code", text)
        self.assertNotIn("guard@uni.test", text)
        self.assertNotIn(PW, text)
        self.assertIsNone(re.search(r"\b\d{6}\b", text))

    def test_used_code_cannot_be_reused(self):
        self.client.post(reverse("staff_login"), {"username": "guard", "password": PW})
        code = code_from_mail()
        self.client.post(reverse("staff_login_verify"), {"code": code})
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("logout"))
        r = self.client.post(reverse("staff_login_verify"), {"code": code})
        self.assertRedirects(r, reverse("staff_login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_app_password_spaces_are_ignored(self):
        import importlib
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"EMAIL_HOST_PASSWORD": "abcd efgh ijkl mnop"}):
            import config.settings as cfg
            importlib.reload(cfg)
            self.assertEqual(cfg.EMAIL_HOST_PASSWORD, "abcdefghijklmnop")
        importlib.reload(cfg)


class OverviewCountTests(LFTestCase):
    def test_overview_counts_reports_and_registrations_by_type(self):
        UserProfile.objects.create(user=self.student, member_type="student")
        UserProfile.objects.create(user=self.finder, member_type="staff")
        self.lost_item()                                  # student, pending
        self.found_item()                                 # teacher (finder), verified on custody receipt
        self.client.force_login(self.sysadmin)
        counts = self.client.get(reverse("admin_overview")).context["counts"]
        self.assertEqual(counts["pipeline"]["total"], Item.objects.count())
        self.assertEqual(counts["pipeline"]["pending"], 1)
        self.assertEqual(counts["pipeline"]["in_progress"], 1)
        students, teachers = counts["groups"]
        self.assertEqual((students["registered"], students["lost"], students["pending"]), (1, 1, 1))
        self.assertEqual((teachers["registered"], teachers["found"]), (1, 1))
