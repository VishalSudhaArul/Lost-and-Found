"""M7 — unclaimed-item ageing, escalation and authorised disposal.

Age is measured from ``custody_since`` (when security received the item).
retention = category override (or its parent's) else default_retention_days,
plus any extension granted during review. Records are never deleted:
disposal writes a DisposalRecord and a DISPOSED custody entry."""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Claim, CustodyRecord, DisposalRecord, Item, OperationalSettings
from . import audit, images, notifications
from .errors import WorkflowError

AGEING_STATUSES = (Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW)


@dataclass
class Ageing:
    item: Item
    age_days: int
    retention_days: int
    days_left: int
    has_active_claims: bool

    @property
    def state(self):
        return "expired" if self.days_left <= 0 else "upcoming"


def retention_for(item, cfg):
    return item.category.effective_retention_days(cfg.default_retention_days) + item.retention_extension_days


def ageing_for(item, cfg, now=None, active_claim_ids=None):
    now = now or timezone.now()
    since = item.custody_since or item.created_at
    age = (now - since).days
    retention = retention_for(item, cfg)
    if active_claim_ids is not None:
        active = item.pk in active_claim_ids
    else:
        active = Claim.objects.filter(item=item, status__in=Claim.ACTIVE_STATUSES).exists()
    return Ageing(item=item, age_days=age, retention_days=retention, days_left=retention - age, has_active_claims=active)


def ageing_queue(cfg=None, now=None, include_all=False):
    cfg = cfg or OperationalSettings.load()
    items = list(Item.objects.found().filter(status__in=AGEING_STATUSES)
                 .select_related("category__parent", "current_custody_location", "disposal_reviewed_by"))
    active_ids = set(Claim.objects.filter(item__in=items, status__in=Claim.ACTIVE_STATUSES).values_list("item_id", flat=True))
    rows = [ageing_for(i, cfg, now, active_ids) for i in items]
    if not include_all:
        rows = [r for r in rows if r.days_left <= cfg.expiry_warning_days or r.item.status == Item.Status.DISPOSAL_REVIEW]
    return sorted(rows, key=lambda r: r.days_left)


def run_ageing_check(now=None):
    """Idempotent scheduled job (management command ``check_ageing``)."""
    cfg = OperationalSettings.load()
    now = now or timezone.now()
    security = list(notifications.users_with_perm("review_disposal"))
    admins = list(notifications.users_with_perm("manage_retention"))
    summary = {"warned": 0, "moved_to_review": 0, "protected_by_claims": 0, "escalated": 0}
    for row in ageing_queue(cfg, now, include_all=True):
        item = row.item
        if 0 < row.days_left <= cfg.expiry_warning_days:
            if notifications.notify(security, "ageing", "Item nearing retention limit",
                                    f"{item.reference} reaches its retention limit in {row.days_left} day(s).",
                                    link="/security/ageing/", dedupe_key=f"ageing-warn:{item.pk}:{row.retention_days}"):
                summary["warned"] += 1
        if row.days_left <= 0 and item.status == Item.Status.IN_CUSTODY:
            if row.has_active_claims:
                summary["protected_by_claims"] += 1
                continue
            with transaction.atomic():
                locked = Item.objects.select_for_update().get(pk=item.pk)
                if locked.status != Item.Status.IN_CUSTODY:
                    continue
                locked.transition_to(Item.Status.DISPOSAL_REVIEW)
            audit.record(None, "ageing.moved_to_review", f"{item.reference} entered the disposal review queue", target=item,
                         age_days=row.age_days, retention_days=row.retention_days)
            notifications.notify(security, "ageing", "Item ready for disposal review",
                                 f"{item.reference} passed its {row.retention_days}-day retention period.",
                                 link="/security/ageing/", dedupe_key=f"ageing-review:{item.pk}:{row.retention_days}")
            summary["moved_to_review"] += 1
        if row.days_left <= -cfg.escalation_days:
            if notifications.notify(admins, "ageing", "Overdue unclaimed item",
                                    f"{item.reference} is {-row.days_left} day(s) past retention and not yet disposed.",
                                    link="/security/ageing/", dedupe_key=f"ageing-escalate:{item.pk}:{row.retention_days}"):
                summary["escalated"] += 1
    return summary


def confirm_review(item_id, actor, request=None):
    """Security confirms the item has been physically checked and is eligible."""
    if not actor.has_perm("lostfound.review_disposal"):
        raise PermissionDenied
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if item.status != Item.Status.DISPOSAL_REVIEW:
            raise WorkflowError("This item is not in the disposal review queue.")
        if Claim.objects.filter(item=item, status__in=Claim.ACTIVE_STATUSES).exists():
            raise WorkflowError("This item has an active claim and cannot be disposed.")
        item.disposal_reviewed_by, item.disposal_reviewed_at = actor, timezone.now()
        item.save(update_fields=["disposal_reviewed_by", "disposal_reviewed_at", "updated_at"])
    audit.record(actor, "disposal.reviewed", f"{item.reference} reviewed for disposal", target=item, request=request)
    return item


def extend_retention(item_id, actor, days, reason, request=None):
    if not actor.has_perm("lostfound.review_disposal"):
        raise PermissionDenied
    days = int(days)
    if not 1 <= days <= 365:
        raise WorkflowError("Extend by between 1 and 365 days.")
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if item.status not in AGEING_STATUSES:
            raise WorkflowError("Only items in custody can have their retention extended.")
        item.retention_extension_days += days
        item.disposal_reviewed_by, item.disposal_reviewed_at = None, None
        if item.status == Item.Status.DISPOSAL_REVIEW:
            item.transition_to(Item.Status.IN_CUSTODY, save=False)
        item.save()
    audit.record(actor, "disposal.retention_extended", f"{item.reference} retention extended by {days} days",
                 target=item, request=request, days=days, reason=(reason or "")[:200])
    return item


def dispose(item_id, actor, method, reason, recipient="", evidence=None, request=None):
    if not actor.has_perm("lostfound.authorize_disposal"):
        raise PermissionDenied
    if not (reason or "").strip():
        raise WorkflowError("A disposal reason is required.")
    if method not in DisposalRecord.Method.values:
        raise WorkflowError("Choose a disposal method.")
    cfg = OperationalSettings.load()
    evidence_file = None
    if evidence:
        try:
            evidence_file = images.process_upload(evidence)
        except ValidationError as exc:
            raise WorkflowError(" ".join(exc.messages))
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if item.status != Item.Status.DISPOSAL_REVIEW:
            raise WorkflowError("Only items in the disposal review queue can be disposed.")
        if Claim.objects.filter(item=item, status__in=Claim.ACTIVE_STATUSES + (Claim.Status.COMPLETED,)).exists():
            raise WorkflowError("Items with active or completed claims cannot be disposed.")
        if item.disposal_reviewed_by_id is None:
            raise WorkflowError("A security review must be recorded before disposal.")
        row = ageing_for(item, cfg)
        if row.days_left > 0:
            raise WorkflowError(f"The retention period has {row.days_left} day(s) remaining.")
        record = DisposalRecord(item=item, method=method, reason=reason.strip(), recipient=recipient,
                                reviewed_by=item.disposal_reviewed_by, authorized_by=actor)
        if evidence_file:
            record.evidence.save(evidence_file.name, evidence_file, save=False)
        record.save()
        CustodyRecord.objects.create(item=item, action=CustodyRecord.Action.DISPOSED, from_location=item.current_custody_location,
                                     accepted_by=actor, notes=f"Disposed: {record.get_method_display()}")
        item.transition_to(Item.Status.DISPOSED, save=False)
        item.current_custody_location = None
        item.save()
    audit.record(actor, "disposal.recorded", f"{item.reference} disposed ({method})", target=item, request=request, method=method)
    notifications.notify(list(notifications.users_with_perm("review_disposal")), "disposal", "Item disposed",
                         f"{item.reference} was disposed ({record.get_method_display()}).", link="/security/ageing/")
    return record
