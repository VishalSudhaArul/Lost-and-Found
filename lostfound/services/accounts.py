"""Administrator account actions: block/unblock and delete.

* Blocking makes an account inactive and signs it out everywhere at once.
* Deleting removes the account outright when it has no history. When reports,
  claims, custody records, points or audit entries reference it, those records
  must be kept for accountability, so the account is anonymised instead: personal
  details are erased, roles removed, and it can never sign in again.
* Nobody can block or delete themselves or a server superuser from the portal."""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from . import audit
from .errors import WorkflowError

User = get_user_model()


def _require(actor, target):
    if not actor.has_perm("lostfound.manage_users"):
        raise PermissionDenied
    if target.pk == actor.pk:
        raise WorkflowError("You can't block or delete your own account.")
    if target.is_superuser:
        raise WorkflowError("Server superuser accounts can't be blocked or deleted from the portal.")


def end_sessions(user):
    """Sign the user out on every device."""
    ended = 0
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if session.get_decoded().get("_auth_user_id") == str(user.pk):
            session.delete()
            ended += 1
    return ended


def block(target, actor, reason, request=None):
    _require(actor, target)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise WorkflowError("Give a short reason for blocking (it's kept in the audit log).")
    target.is_active = False
    target.save(update_fields=["is_active"])
    ended = end_sessions(target)
    audit.record(actor, "users.blocked", f"Account {target.username} blocked", target=target, request=request,
                 reason=reason[:200], sessions_ended=ended)
    return ended


def unblock(target, actor, request=None):
    _require(actor, target)
    target.is_active = True
    target.save(update_fields=["is_active"])
    audit.record(actor, "users.unblocked", f"Account {target.username} unblocked", target=target, request=request)


def delete(target, actor, confirm_username, request=None):
    """Returns "deleted" or "anonymised"."""
    _require(actor, target)
    if (confirm_username or "").strip() != target.username:
        raise WorkflowError("Type the account's username exactly to confirm.")
    username, pk = target.username, target.pk
    end_sessions(target)
    try:
        with transaction.atomic():
            target.delete()
        audit.record(actor, "users.deleted", f"Account {username} (#{pk}) deleted", request=request)
        return "deleted"
    except ProtectedError:
        pass  # history references this account: keep the records, erase the person
    with transaction.atomic():
        target.refresh_from_db()
        target.username = f"deleted-user-{pk}"
        target.first_name, target.last_name = "Deleted", "user"
        target.email = ""
        target.is_active = False
        target.is_staff = False
        target.set_unusable_password()
        target.save()
        target.groups.clear()
        target.user_permissions.clear()
        profile = getattr(target, "profile", None)
        if profile is not None:
            profile.department = ""
            profile.save(update_fields=["department", "updated_at"])
        from ..models import Notification, UserRewardProfile
        Notification.objects.filter(user=target).delete()
        UserRewardProfile.objects.update_or_create(user=target, defaults={"display_name": "",
                                                                          "show_on_leaderboard": False})
    audit.record(actor, "users.anonymised", f"Account {username} (#{pk}) deleted; history kept anonymously",
                 target=target, request=request)
    return "anonymised"
