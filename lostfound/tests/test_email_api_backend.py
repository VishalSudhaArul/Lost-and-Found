"""HTTPS email backend (Brevo) used where SMTP ports are blocked. No real network calls."""
import io
import json
import urllib.error
from unittest import mock

from django.core.mail import EmailMultiAlternatives
from django.test import override_settings
from django.urls import reverse

from lostfound import mail_backends
from lostfound.services import mailer

from .base import LFTestCase

BREVO = dict(EMAIL_BACKEND="lostfound.mail_backends.BrevoEmailBackend", BREVO_API_KEY="test-key-not-real",
             DEFAULT_FROM_EMAIL="Campus Lost & Found <desk@uni.test>")


class _Resp:
    def __init__(self, status=201, body=b'{"messageId":"<x@brevo>"}'):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(status, code):
    return urllib.error.HTTPError(mail_backends.BREVO_SEND_URL, status, "err", {},
                                  io.BytesIO(json.dumps({"code": code, "message": "details"}).encode()))


@override_settings(**BREVO)
class BrevoBackendTests(LFTestCase):
    def test_mailer_sends_over_https_with_expected_payload(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
            ok = mailer.send("Your code", "Your one-time code is 123456.", "student@uni.test", purpose="sign-in code")
        self.assertTrue(ok)
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Api-key"), "test-key-not-real")
        body = json.loads(req.data)
        self.assertEqual(body["sender"], {"email": "desk@uni.test", "name": "Campus Lost & Found"})
        self.assertEqual(body["to"], [{"email": "student@uni.test"}])
        self.assertEqual(body["subject"], "Your code")
        self.assertIn("123456", body["textContent"])

    def test_html_alternative_is_included(self):
        msg = EmailMultiAlternatives("Hi", "text", None, ["a@uni.test"])
        msg.attach_alternative("<p>html</p>", "text/html")
        with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
            msg.send()
        self.assertEqual(json.loads(urlopen.call_args[0][0].data)["htmlContent"], "<p>html</p>")

    def test_api_rejection_is_logged_safely_and_reported_as_failure(self):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "unauthorized")), \
                self.assertLogs("lostfound.email", level="ERROR") as logs:
            ok = mailer.send("Your code", "code 654321", "student@uni.test", purpose="staff sign-in code")
        self.assertFalse(ok)
        text = " ".join(logs.output)
        self.assertIn("HTTP 401", text)
        self.assertIn("unauthorized", text)
        for secret in ("student@uni.test", "654321", "test-key-not-real"):
            self.assertNotIn(secret, text)

    def test_network_failure_is_reported_without_raising(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")), \
                self.assertLogs("lostfound.email", level="ERROR"):
            self.assertFalse(mailer.send("s", "b", "student@uni.test", purpose="test"))

    def test_staff_two_step_code_is_delivered_through_the_api(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp()) as urlopen:
            r = self.client.post(reverse("staff_login"), {"username": "guard", "password": "pw-Long-enough-1"})
        self.assertRedirects(r, reverse("staff_login_verify"))
        self.assertNotIn("_auth_user_id", self.client.session)  # two-step still required
        self.assertEqual(json.loads(urlopen.call_args[0][0].data)["to"], [{"email": "guard@uni.test"}])

    def test_api_failure_still_blocks_staff_sign_in(self):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(400, "invalid_parameter")), \
                self.assertLogs("lostfound.email", level="ERROR"):
            r = self.client.post(reverse("staff_login"), {"username": "guard", "password": "pw-Long-enough-1"})
        self.assertContains(r, "send your sign-in code")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_diagnose_checks_the_key_without_sending(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(200, b'{"email":"x"}')) as urlopen:
            ok, msg = mailer.diagnose()
        self.assertTrue(ok)
        self.assertEqual(urlopen.call_args[0][0].get_method(), "GET")
        self.assertIn("desk@uni.test", msg)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "unauthorized")):
            ok, msg = mailer.diagnose()
        self.assertFalse(ok)
        self.assertIn("rejected the API key", msg)
        self.assertNotIn("test-key-not-real", msg)
