"""Google Apps Script email relay (free HTTPS route from your own Gmail). No real network calls."""
import json
import urllib.error
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from lostfound.services import mailer

from .base import LFTestCase

RELAY = dict(EMAIL_BACKEND="lostfound.mail_backends.AppsScriptEmailBackend",
             APPS_SCRIPT_MAIL_URL="https://script.google.com/macros/s/TEST/exec",
             APPS_SCRIPT_MAIL_SECRET="relay-secret-not-real",
             DEFAULT_FROM_EMAIL="Campus Lost & Found <desk@uni.test>")


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(**extra):
    return _Resp(json.dumps({"ok": True, **extra}).encode())


@override_settings(**RELAY)
class AppsScriptRelayTests(LFTestCase):
    def test_sends_to_the_relay_with_secret_and_message(self):
        with mock.patch("urllib.request.urlopen", return_value=ok()) as urlopen:
            self.assertTrue(mailer.send("Your code", "Your one-time code is 123456.", "student@uni.test", purpose="code"))
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, RELAY["APPS_SCRIPT_MAIL_URL"])
        self.assertEqual(req.get_method(), "POST")
        body = json.loads(req.data)
        self.assertEqual(body["secret"], "relay-secret-not-real")
        self.assertEqual((body["action"], body["to"], body["subject"], body["name"]),
                         ("send", "student@uni.test", "Your code", "Campus Lost & Found"))
        self.assertIn("123456", body["body"])

    def test_refusals_are_failures_and_logs_stay_clean(self):
        for reply in (_Resp(b'{"ok": false, "code": "bad_secret"}'), _Resp(b"<html>Sign in</html>"),
                      urllib.error.URLError("down")):
            effect = {"side_effect": reply} if isinstance(reply, Exception) else {"return_value": reply}
            with mock.patch("urllib.request.urlopen", **effect), \
                    self.assertLogs("lostfound.email", level="ERROR") as logs:
                self.assertFalse(mailer.send("s", "code 654321", "student@uni.test", purpose="sign-in code"))
            text = " ".join(logs.output)
            for secret in ("student@uni.test", "654321", "relay-secret-not-real"):
                self.assertNotIn(secret, text)

    def test_staff_two_step_uses_the_relay_and_stays_required(self):
        with mock.patch("urllib.request.urlopen", return_value=ok()) as urlopen:
            r = self.client.post(reverse("staff_login"), {"username": "guard", "password": "pw-Long-enough-1"})
        self.assertRedirects(r, reverse("staff_login_verify"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(json.loads(urlopen.call_args[0][0].data)["to"], "guard@uni.test")

    def test_relay_failure_blocks_staff_sign_in(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(b'{"ok": false, "code": "quota_exceeded"}')), \
                self.assertLogs("lostfound.email", level="ERROR"):
            r = self.client.post(reverse("staff_login"), {"username": "guard", "password": "pw-Long-enough-1"})
        self.assertContains(r, "send your sign-in code")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_diagnose_pings_without_sending(self):
        with mock.patch("urllib.request.urlopen", return_value=ok(quota=97)) as urlopen:
            good, msg = mailer.diagnose()
        self.assertTrue(good)
        self.assertEqual(json.loads(urlopen.call_args[0][0].data)["action"], "ping")
        self.assertIn("97 emails left", msg)
        with mock.patch("urllib.request.urlopen", return_value=_Resp(b'{"ok": false, "code": "bad_secret"}')):
            good, msg = mailer.diagnose()
        self.assertFalse(good)
        self.assertIn("SECRET", msg)
        self.assertNotIn("relay-secret-not-real", msg)


@override_settings(EMAIL_BACKEND="lostfound.mail_backends.AppsScriptEmailBackend", APPS_SCRIPT_MAIL_URL="",
                   APPS_SCRIPT_MAIL_SECRET="")
class AppsScriptMissingSettingsTests(LFTestCase):
    def test_diagnose_explains_missing_settings(self):
        good, msg = mailer.diagnose()
        self.assertFalse(good)
        self.assertIn("APPS_SCRIPT_MAIL_URL", msg)


class UnconfiguredProductionEmailTests(LFTestCase):
    @override_settings(EMAIL_BACKEND="lostfound.mail_backends.UnconfiguredEmailBackend")
    def test_codes_are_never_printed_and_sign_in_stays_blocked(self):
        with self.assertLogs("lostfound.email", level="ERROR") as logs:
            r = self.client.post(reverse("staff_login"), {"username": "guard", "password": "pw-Long-enough-1"})
        self.assertContains(r, "send your sign-in code")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIn("not configured", " ".join(logs.output))
        self.assertNotIn("guard@uni.test", " ".join(logs.output))
        good, msg = mailer.diagnose()
        self.assertFalse(good)
        self.assertIn("nothing is sent or printed", msg)
