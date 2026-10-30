from django.db import IntegrityError, transaction

from ..models.audit import Notification


def notify(users, kind, title, message, link="", dedupe_key=""):
    """Create in-app notifications. Messages must never contain private
    details, verification answers or handover codes. ``dedupe_key`` makes the
    call idempotent per user (used by scheduled jobs)."""
    created = []
    seen = set()
    for user in users:
        if user is None or user.pk in seen or not user.is_active:
            continue
        seen.add(user.pk)
        if dedupe_key and Notification.objects.filter(user=user, dedupe_key=dedupe_key).exists():
            continue
        try:
            with transaction.atomic():
                created.append(Notification.objects.create(
                    user=user, kind=kind, title=title[:140], message=message[:500], link=link, dedupe_key=dedupe_key))
        except IntegrityError:
            continue
        _email(user, kind, title, message, link)
    return created


EMAIL_KINDS = {"claim", "handover", "verification", "account", "report"}


def _email(user, kind, title, message, link):
    """Send the same (non-sensitive) notification to the user's registered email
    through the central mailer. Failures are logged by the mailer, never raised."""
    from django.conf import settings

    from . import mailer
    if kind not in EMAIL_KINDS or not user.email or not getattr(settings, "EMAIL_NOTIFICATIONS", True):
        return
    url = f"{settings.SITE_URL.rstrip('/')}{link}" if link and getattr(settings, "SITE_URL", "") else ""
    body = (f"Hello {user.get_full_name() or user.get_username()},\n\n{message}\n\n"
            + (f"Open it here: {url}\n\n" if url else "")
            + "You are receiving this because you have an account on Campus Lost & Found.")
    mailer.send(f"Campus Lost & Found: {title}", body, user.email, purpose=f"{kind} notification")


def users_with_perm(codename):
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    User = get_user_model()
    return User.objects.filter(
        Q(groups__permissions__codename=codename) | Q(user_permissions__codename=codename) | Q(is_superuser=True),
        is_active=True,
    ).distinct()
