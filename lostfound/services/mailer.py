"""One place to send email, with failures logged safely.

Logs say *why* a send failed (error type, SMTP status code) and for which
purpose, but never the recipient address, subject body, password, sign-in code
or reset/activation token."""
import logging
import smtplib
import ssl

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("lostfound.email")


def send(subject, body, recipient, purpose):
    """Send one plain-text email. Returns True on success, False on failure (logged)."""
    try:
        send_mail(subject, body, None, [recipient], fail_silently=False)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("Email (%s) not sent: the mail server rejected the login (SMTP %s). "
                     "Check EMAIL_HOST_USER / EMAIL_HOST_PASSWORD (Gmail needs an App password).", purpose, exc.smtp_code)
    except smtplib.SMTPServerDisconnected:
        logger.error("Email (%s) not sent: the mail server closed the connection. With Gmail this usually "
                     "means the App password was rejected; run `python manage.py check_email`.", purpose)
    except smtplib.SMTPException as exc:
        logger.error("Email (%s) not sent: SMTP error %s (%s).", purpose, type(exc).__name__,
                     getattr(exc, "smtp_code", "-"))
    except OSError as exc:
        logger.error("Email (%s) not sent: could not reach %s:%s (%s).", purpose, settings.EMAIL_HOST,
                     settings.EMAIL_PORT, type(exc).__name__)
    return False


def diagnose():
    """Check SMTP connectivity and login without sending anything.
    Uses AUTH PLAIN directly so the server's real answer (e.g. 535) is reported
    instead of the "connection closed" that smtplib shows after a rejection."""
    import base64
    backend = settings.EMAIL_BACKEND
    if not backend.endswith("smtp.EmailBackend"):
        if getattr(settings, "EMAIL_SMTP_INCOMPLETE", False):
            return False, "SMTP is selected but EMAIL_HOST_PASSWORD is empty, so emails go to the console."
        return False, f"EMAIL_BACKEND is {backend}: emails are not delivered over SMTP."
    host, port = settings.EMAIL_HOST, settings.EMAIL_PORT
    try:
        server = (smtplib.SMTP_SSL(host, port, timeout=settings.EMAIL_TIMEOUT, context=ssl.create_default_context())
                  if getattr(settings, "EMAIL_USE_SSL", False) else smtplib.SMTP(host, port, timeout=settings.EMAIL_TIMEOUT))
        server.ehlo()
        if settings.EMAIL_USE_TLS:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
    except OSError as exc:
        return False, f"Can't connect to {host}:{port} ({type(exc).__name__}). Check the network or firewall."
    token = base64.b64encode(f"\0{settings.EMAIL_HOST_USER}\0{settings.EMAIL_HOST_PASSWORD}".encode()).decode()
    try:
        code, reply = server.docmd("AUTH", "PLAIN " + token)
    except smtplib.SMTPServerDisconnected:
        return False, "The server closed the connection during login."
    finally:
        try:
            server.close()
        except Exception:
            pass
    if code == 235:
        return True, f"Login to {host} as {settings.EMAIL_HOST_USER} succeeded."
    text = reply.decode(errors="ignore").split("\n")[0][:120]
    if code == 535:
        return False, (f"{host} rejected the username or password (SMTP 535: {text}). For Gmail, create a new "
                       "App password while signed in to EMAIL_HOST_USER and paste it into EMAIL_HOST_PASSWORD in .env.")
    return False, f"Login failed (SMTP {code}: {text})."
