"""Email delivery over HTTPS, for hosts that block SMTP ports (e.g. Render's free web services).

Brevo (https://www.brevo.com) transactional email API, called with the standard library only:

    EMAIL_BACKEND=lostfound.mail_backends.BrevoEmailBackend
    BREVO_API_KEY=<key from Brevo > SMTP & API > API keys>
    DEFAULT_FROM_EMAIL=Campus Lost & Found <address verified as a sender in Brevo>

Every email still goes through lostfound.services.mailer, so failures are logged without
recipients, codes or links. The API key is sent only in the request header, never logged.
"""
import json
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_ACCOUNT_URL = "https://api.brevo.com/v3/account"


class EmailApiError(OSError):
    """The email API refused or could not be reached. ``status`` is the HTTP status (0 = no response);
    ``code`` is the API's short error code (e.g. ``unauthorized``), never message content."""

    def __init__(self, status, code):
        super().__init__(f"email API error {status} ({code})")
        self.status, self.code = status, code


def _contact(address):
    name, email = parseaddr(address)
    return {"email": email, **({"name": name} if name else {})}


def _request(url, payload=None, timeout=None):
    key = getattr(settings, "BREVO_API_KEY", "")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET", headers={
        "api-key": key, "accept": "application/json", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or getattr(settings, "EMAIL_TIMEOUT", 15)) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            code = json.loads(exc.read() or b"{}").get("code", "error")
        except ValueError:
            code = "error"
        raise EmailApiError(exc.code, str(code)[:40]) from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise EmailApiError(0, type(exc).__name__) from None


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages or []:
            try:
                self._send(message)
                sent += 1
            except EmailApiError:
                if not self.fail_silently:
                    raise
        return sent

    def _send(self, message):
        recipients = [_contact(a) for a in message.to if a]
        if not recipients:
            return
        payload = {
            "sender": _contact(message.from_email or settings.DEFAULT_FROM_EMAIL),
            "to": recipients,
            "subject": message.subject,
            "textContent": message.body,
        }
        if message.cc:
            payload["cc"] = [_contact(a) for a in message.cc]
        if message.bcc:
            payload["bcc"] = [_contact(a) for a in message.bcc]
        if message.reply_to:
            payload["replyTo"] = _contact(message.reply_to[0])
        for content, mimetype in getattr(message, "alternatives", []) or []:
            if mimetype == "text/html":
                payload["htmlContent"] = content
        _request(BREVO_SEND_URL, payload)


def check_brevo():
    """(ok, message) for the admin settings page and `check_email`; sends nothing."""
    if not getattr(settings, "BREVO_API_KEY", ""):
        return False, "BREVO_API_KEY is not set, so emails are printed in the server console."
    try:
        _request(BREVO_ACCOUNT_URL)
    except EmailApiError as exc:
        if exc.status in (401, 403):
            return False, f"Brevo rejected the API key (HTTP {exc.status}, {exc.code}). Create a new key and set BREVO_API_KEY."
        return False, f"Could not reach the Brevo API (HTTP {exc.status or 'no response'}, {exc.code})."
    sender = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
    return True, (f"Brevo API key accepted. Emails are sent from {sender}; that address must be a verified "
                  "sender in Brevo.")


__all__ = ["BrevoEmailBackend", "EmailApiError", "check_brevo"]
