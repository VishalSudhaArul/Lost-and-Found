"""Account-specific password recovery, member two-step sign-in and central email delivery."""
import re
import smtplib
import time
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from lostfound import roles
from lostfound.models import UserProfile
from lostfound.services import claims, notifications

from .base import LFTestCase, User

PW = "pw-Long-enough-1"
GENERIC = "If it matches an account"


def code_from_mail():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class PasswordRecoveryTests(LFTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_superuser("rootadmin", "root.admin@uni.test", PW)
        UserProfile.objects.create(user=cls.student, member_type=UserProfile.MemberType.STUDENT)
        cls.teacher = cls.make_user("teacher", roles.MEMBERS)
        UserProfile.objects.create(user=cls.teacher, member_type=UserProfile.MemberType.STAFF)

    def reset(self, identifier, **extra):
        return self.client.post(reverse("password_reset") + extra.pop("query", ""), {"email": identifier, **extra})

    def test_admin_reset_by_username_goes_to_registered_email(self):
        self.reset("rootadmin")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["root.admin@uni.test"])
        self.assertIn("/accounts/password/reset/", mail.outbox[0].body)

    def test_admin_reset_by_email_goes_to_registered_email(self):
        self.reset("ROOT.ADMIN@uni.test")
        self.assertEqual([m.to for m in mail.outbox], [["root.admin@uni.test"]])

    def test_student_and_teacher_resets_go_to_their_own_email(self):
        self.reset("student")
        self.reset("teacher@uni.test")
        self.assertEqual([m.to for m in mail.outbox], [["student@uni.test"], ["teacher@uni.test"]])

    def test_tampering_cannot_redirect_the_link(self):
        # extra fields, query parameters and look-alike addresses never become the recipient
        self.reset("rootadmin", to="attacker@evil.test", recipient="attacker@evil.test",
                   query="?email=attacker@evil.test&to=attacker@evil.test")
        self.reset("rootadmin ")
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertEqual(recipients, {"root.admin@uni.test"})
        mail.outbox.clear()
        self.reset("root.admin@uni.test.evil.test")
        self.reset("attacker@evil.test")
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_inactive_and_passwordless_accounts_get_the_same_response(self):
        known = self.reset("student")
        inactive = self.make_user("gone", roles.MEMBERS)
        inactive.is_active = False
        inactive.save()
        pending = self.make_user("pending", roles.SECURITY)
        pending.set_unusable_password()
        pending.save()
        mail.outbox.clear()
        for ident in ("nobody", "nobody@uni.test", "gone", "pending", ""):
            r = self.reset(ident)
            if ident:
                self.assertEqual(r.status_code, known.status_code, ident)
                self.assertEqual(r.url, known.url, ident)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(self.client.get(reverse("password_reset")), GENERIC)

    def test_reset_does_not_sign_in_or_bypass_staff_two_step(self):
        self.reset("guard")
        link = re.search(r"https?://[^/]+(/accounts/password/reset/\S+/)", mail.outbox[-1].body).group(1)
        r = self.client.get(link, follow=True)
        self.client.post(r.redirect_chain[-1][0], {"new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("staff_login"), {"username": "guard", "password": "Brand-new-pass-77"})
        self.assertRedirects(r, reverse("staff_login_verify"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_triggered_reset_targets_that_account(self):
        self.client.force_login(self.sysadmin)
        self.client.post(reverse("admin_user_edit", args=[self.teacher.pk]), {"action": "reset"})
        self.assertEqual(mail.outbox[-1].to, ["teacher@uni.test"])

    def test_delivery_failure_is_logged_safely_with_generic_response(self):
        with mock.patch("lostfound.services.mailer.send_mail",
                        side_effect=smtplib.SMTPAuthenticationError(535, b"bad")), \
                self.assertLogs("lostfound.email", level="ERROR") as logs:
            r = self.reset("student")
        self.assertEqual(r.status_code, 302)
        text = " ".join(logs.output)
        self.assertIn("password reset", text)
        self.assertNotIn("student@uni.test", text)
        self.assertNotIn("/reset/", text)


class MemberTwoStepTests(LFTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        UserProfile.objects.create(user=cls.student, member_type=UserProfile.MemberType.STUDENT, two_step_enabled=True)
        cls.teacher = cls.make_user("teacher", roles.MEMBERS)
        UserProfile.objects.create(user=cls.teacher, member_type=UserProfile.MemberType.STAFF, two_step_enabled=True)
        cls.admin = User.objects.create_superuser("rootadmin", "root.admin@uni.test", PW)

    def test_student_code_goes_to_student_email_and_login_waits_for_it(self):
        r = self.client.post(reverse("student_login"), {"username": "student", "password": PW})
        self.assertRedirects(r, reverse("login_verify"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(mail.outbox[-1].to, ["student@uni.test"])
        r = self.client.post(reverse("login_verify"), {"code": code_from_mail()})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.student.pk)

    def test_teacher_code_goes_to_teacher_email_not_admin(self):
        self.client.post(reverse("teacher_login"), {"username": "teacher", "password": PW})
        self.assertEqual(mail.outbox[-1].to, ["teacher@uni.test"])
        self.assertNotIn("root.admin@uni.test", [a for m in mail.outbox for a in m.to])

    def test_generic_sign_in_page_cannot_skip_two_step(self):
        r = self.client.post(reverse("login"), {"username": "student", "password": PW})
        self.assertRedirects(r, reverse("login_verify"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_code_goes_only_to_admin_email(self):
        self.client.post(reverse("staff_login"), {"username": "rootadmin", "password": PW})
        self.assertEqual([m.to for m in mail.outbox], [["root.admin@uni.test"]])
        code = code_from_mail()
        self.assertEqual(len(code), 6)

    @override_settings(STAFF_CODE_MAX_ATTEMPTS=5)
    def test_attempt_limit_and_single_use(self):
        self.client.post(reverse("student_login"), {"username": "student", "password": PW})
        code = code_from_mail()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.client.post(reverse("login_verify"), {"code": wrong})
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("login_verify"), {"code": wrong})  # 5th wrong attempt ends it
        self.assertRedirects(r, reverse("student_login"))
        r = self.client.post(reverse("login_verify"), {"code": code})
        self.assertNotIn("_auth_user_id", self.client.session)
        # fresh code works exactly once
        self.client.post(reverse("student_login"), {"username": "student", "password": PW})
        code = code_from_mail()
        self.client.post(reverse("login_verify"), {"code": code})
        self.assertIn("_auth_user_id", self.client.session)
        self.client.logout()
        self.client.post(reverse("login_verify"), {"code": code})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_code_expires_after_ttl(self):
        self.client.post(reverse("student_login"), {"username": "student", "password": PW})
        code = code_from_mail()
        with mock.patch("lostfound.views.accounts.time.time", return_value=time.time() + 601):
            r = self.client.post(reverse("login_verify"), {"code": code})
        self.assertRedirects(r, reverse("student_login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_members_without_two_step_sign_in_directly(self):
        UserProfile.objects.create(user=self.other, member_type=UserProfile.MemberType.STUDENT)
        r = self.client.post(reverse("student_login"), {"username": "other", "password": PW})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

    def test_profile_toggle(self):
        UserProfile.objects.create(user=self.other, member_type=UserProfile.MemberType.STUDENT)
        self.client.force_login(self.other)
        self.client.post(reverse("profile"), {"first_name": "Other", "two_step_enabled": "on"})
        self.assertTrue(UserProfile.objects.get(user=self.other).two_step_enabled)

    def test_send_failure_blocks_sign_in_and_is_logged_safely(self):
        with mock.patch("lostfound.services.mailer.send_mail", side_effect=OSError("down")), \
                self.assertLogs("lostfound.email", level="ERROR") as logs:
            r = self.client.post(reverse("student_login"), {"username": "student", "password": PW})
        self.assertContains(r, "couldn&#x27;t send your sign-in code")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("student@uni.test", " ".join(logs.output))


class CentralEmailTests(LFTestCase):
    def test_claim_notifications_are_emailed_to_registered_addresses(self):
        item = self.found_item()
        mail.outbox.clear()
        claim = claims.submit_claim(item, self.student, "Black backpack with a sticker", where_when="Library")
        staff_mail = [m for m in mail.outbox if "guard@uni.test" in m.to]
        self.assertTrue(staff_mail)
        mail.outbox.clear()
        claims.request_info(claim.pk, self.guard, "What is inside the front pocket?")
        self.assertEqual([m.to for m in mail.outbox], [["student@uni.test"]])
        self.assertNotIn("zebra", mail.outbox[0].body)

    @override_settings(EMAIL_NOTIFICATIONS=False)
    def test_notification_email_can_be_switched_off(self):
        notifications.notify([self.student], "claim", "Hi", "Message")
        self.assertEqual(len(mail.outbox), 0)
