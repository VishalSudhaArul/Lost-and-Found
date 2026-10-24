"""Lost & Found team review of new reports.

Every report starts as "awaiting team review". The team verifies it (it stays on,
or joins, the board and the finder earns recognition) or rejects it (it's hidden
from the board and earns nothing). Physically receiving a found item into custody
also verifies its report."""
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from ..models import Item
from . import audit, matching, notifications, rewards
from .errors import WorkflowError


def _require(user):
    if not user.has_perm("lostfound.review_reports"):
        raise PermissionDenied


def verify(item_id, actor, note="", quality_bonus=False, request=None, auto=False):
    if not auto:
        _require(actor)
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if item.reporter_id == actor.pk and not auto:
            raise WorkflowError("Another team member must review your own report.")
        if item.review_status == Item.Review.VERIFIED:
            return item
        item.review_status = Item.Review.VERIFIED
        item.reviewed_by, item.reviewed_at = actor, timezone.now()
        item.review_note = (note or "")[:300]
        item.is_hidden = False
        item.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "review_note", "is_hidden", "updated_at"])
    audit.record(actor, "report.verified", f"{item.reference} verified{' (custody receipt)' if auto else ''}",
                 target=item, request=request)
    notifications.notify([item.reporter], "system", "Your report was verified",
                         f"The Lost & Found team verified {item.reference}.", link=f"/my/reports/{item.pk}/")
    rewards.on_found_report_verified(item, actor)
    if quality_bonus:
        rewards.on_quality_bonus(item, actor)
    if not auto:  # custody receipt refreshes matching itself
        matching.refresh_for_item(item)
    return item


def reject(item_id, actor, reason, request=None):
    _require(actor)
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise WorkflowError("Give the reporter a short reason.")
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if item.reporter_id == actor.pk:
            raise WorkflowError("Another team member must review your own report.")
        if item.status != Item.Status.OPEN:
            raise WorkflowError("Only open reports can be rejected; items in custody follow the custody process.")
        item.review_status = Item.Review.REJECTED
        item.reviewed_by, item.reviewed_at = actor, timezone.now()
        item.review_note = reason[:300]
        item.is_hidden = True
        item.save(update_fields=["review_status", "reviewed_by", "reviewed_at", "review_note", "is_hidden", "updated_at"])
    audit.record(actor, "report.rejected", f"{item.reference} rejected", target=item, request=request)
    notifications.notify([item.reporter], "system", "Your report needs attention",
                         f"{item.reference} wasn't published: {reason}", link=f"/my/reports/{item.pk}/")
    matching.refresh_for_item(item)
    return item
