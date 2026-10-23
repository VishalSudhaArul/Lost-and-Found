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
    return created


def users_with_perm(codename):
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    User = get_user_model()
    return User.objects.filter(
        Q(groups__permissions__codename=codename) | Q(user_permissions__codename=codename) | Q(is_superuser=True),
        is_active=True,
    ).distinct()
