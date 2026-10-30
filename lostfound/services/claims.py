"""M5 — claim workflow with competing-claim safety.

Concurrency: approval locks the item row and all its claims with
SELECT ... FOR UPDATE, and the partial unique constraint
``uniq_approved_claim_per_item`` makes a second approval impossible even if
application logic were bypassed."""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Claim, ClaimMessage, Handover, Item, MatchSuggestion, VerificationChallenge
from . import audit, notifications, ratelimit, verification
from .errors import WorkflowError

CLAIMABLE_STATUSES = (Item.Status.OPEN, Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW)


def can_claim(item, user):
    if not item.is_found or item.status not in CLAIMABLE_STATUSES or item.is_hidden:
        return False, "This item is not open for claims."
    if item.reporter_id == user.pk:
        return False, "You reported finding this item, so you cannot claim it."
    if Claim.objects.filter(item=item, claimant=user, status__in=Claim.ACTIVE_STATUSES).exists():
        return False, "You already have an active claim on this item."
    return True, ""


def _cancel_open_challenge(claim):
    VerificationChallenge.objects.filter(claim=claim, status=VerificationChallenge.Status.OPEN).update(
        status=VerificationChallenge.Status.CANCELLED)


def submit_claim(item, user, statement, lost_report=None, request=None, where_when="", extra_info="",
                 attachment=None, with_challenge=False, lost_when=""):
    """Create a claim. By default it goes straight to security review (staff verify ownership
    against the private details); ``with_challenge`` keeps the legacy self-service quiz."""
    if lost_report is not None and (lost_report.reporter_id != user.pk or not lost_report.is_lost):
        raise PermissionDenied("You can only link your own lost reports.")
    limit, window = settings.CLAIM_RATE_LIMIT
    try:
        ratelimit.hit("claim", user.pk, limit, window)
    except ratelimit.RateLimited:
        raise WorkflowError("You have submitted too many claims recently. Please try again later.")
    try:
        with transaction.atomic():
            item = Item.objects.select_for_update().get(pk=item.pk)
            ok, why = can_claim(item, user)
            if not ok:
                raise WorkflowError(why)
            claim = Claim(item=item, claimant=user, statement=statement.strip(), lost_report=lost_report,
                          lost_where_when=(where_when or "").strip()[:300], lost_when=(lost_when or "").strip()[:120],
                          extra_info=(extra_info or "").strip(),
                          status=Claim.Status.PENDING_VERIFICATION if with_challenge else Claim.Status.UNDER_REVIEW)
            if attachment:
                claim.attachment.save(attachment.name, attachment, save=False)
            claim.save()
            challenge = verification.issue_challenge(claim) if with_challenge else None
            if lost_report is not None:
                MatchSuggestion.objects.filter(lost_item=lost_report, found_item=item).update(status=MatchSuggestion.Status.CLAIMED)
    except IntegrityError:
        raise WorkflowError("You already have an active claim on this item.")
    audit.record(user, "claim.submitted", f"Claim {claim.reference} submitted on {item.reference}", target=claim, request=request)
    notifications.notify([item.reporter], "claim", "Someone claimed the item you found",
                         f"A claim was submitted on {item.reference}. Campus security will verify ownership.",
                         link=f"/my/reports/{item.pk}/")
    if with_challenge and challenge is None:
        notifications.notify(notifications.users_with_perm("manage_custody"), "verification", "Verification details needed",
                             f"Claim {claim.reference} needs private details on {item.reference} before it can be verified.",
                             link=f"/security/items/{item.pk}/")
    if not with_challenge:
        notifications.notify(notifications.users_with_perm("review_claims"), "claim", "New claim to review",
                             f"Claim {claim.reference} on {item.reference} is waiting for ownership review.",
                             link=f"/security/claims/{claim.pk}/")
    return claim


REVIEWABLE = (Claim.Status.PENDING_VERIFICATION, Claim.Status.UNDER_REVIEW, Claim.Status.INFO_REQUESTED)


def request_info(claim_id, actor, message, request=None):
    """Security asks the claimant for more detail or evidence. Never reveals private details."""
    _require(actor, "review_claims")
    message = (message or "").strip()
    if len(message) < 5:
        raise WorkflowError("Write the question for the claimant.")
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if claim.claimant_id == actor.pk:
            raise PermissionDenied("You cannot review your own claim.")
        if claim.status not in REVIEWABLE:
            raise WorkflowError("You can only ask for information while a claim is being reviewed.")
        _cancel_open_challenge(claim)
        ClaimMessage.objects.create(claim=claim, author=actor, from_staff=True, body=message[:1500])
        claim.status = Claim.Status.INFO_REQUESTED
        claim.save(update_fields=["status", "updated_at"])
    audit.record(actor, "claim.info_requested", f"More information requested on {claim.reference}", target=claim,
                 request=request)
    notifications.notify([claim.claimant], "claim", "Campus security needs a little more information",
                         f"Please answer the question on claim {claim.reference} so it can be reviewed.",
                         link=f"/my/claims/{claim.pk}/")
    return claim


def respond_to_request(claim_id, user, message, request=None):
    message = (message or "").strip()
    if len(message) < 3:
        raise WorkflowError("Write a short answer.")
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if claim.claimant_id != user.pk:
            raise PermissionDenied
        if claim.status != Claim.Status.INFO_REQUESTED:
            raise WorkflowError("Security hasn't asked for more information on this claim.")
        ClaimMessage.objects.create(claim=claim, author=user, from_staff=False, body=message[:1500])
        claim.status = Claim.Status.UNDER_REVIEW
        claim.save(update_fields=["status", "updated_at"])
    audit.record(user, "claim.info_provided", f"Claimant replied on {claim.reference}", target=claim, request=request)
    notifications.notify(notifications.users_with_perm("review_claims"), "claim", "Claimant replied",
                         f"Claim {claim.reference} has new information and is ready for review again.",
                         link=f"/security/claims/{claim.pk}/")
    return claim


def _require(user, perm):
    if not user.has_perm(f"lostfound.{perm}"):
        raise PermissionDenied


def approve_claim(claim_id, actor, reason="", confirm_competing=False, request=None, verified_by_staff=False):
    _require(actor, "decide_claims")
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        item = Item.objects.select_for_update().get(pk=claim.item_id)
        others = list(Claim.objects.select_for_update().filter(item=item, status__in=Claim.ACTIVE_STATUSES).exclude(pk=claim.pk))
        if claim.claimant_id == actor.pk:
            raise PermissionDenied("You cannot decide your own claim.")
        if claim.status not in (Claim.Status.UNDER_REVIEW, Claim.Status.PENDING_VERIFICATION):
            raise WorkflowError("Only claims under review can be approved (not while waiting for the claimant).")
        # A legacy quiz result never blocks manual review: staff compare the evidence themselves.
        override = verified_by_staff and claim.verification_status != Claim.Verification.PASSED
        legacy_result = claim.verification_status
        _cancel_open_challenge(claim)
        if not verified_by_staff and claim.verification_status != Claim.Verification.PASSED:
            raise WorkflowError("Confirm that you compared the claim with the item's private identifying details.")
        if any(o.status == Claim.Status.APPROVED for o in others):
            raise WorkflowError("Another claim on this item is already approved.")
        if item.status not in (Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW):
            raise WorkflowError("The item must be received into custody before a claim can be approved.")
        if others and not confirm_competing:
            raise WorkflowError(f"There are {len(others)} competing claim(s). Confirm that they will be rejected.")
        now = timezone.now()
        for other in others:
            other.status = Claim.Status.REJECTED
            other.decision_reason = "Another claim for this item was verified and approved by campus security."
            other.decided_by, other.decided_at = actor, now
            other.save()
            _cancel_open_challenge(other)
        claim.status = Claim.Status.APPROVED
        if verified_by_staff:
            claim.verification_status = Claim.Verification.PASSED
        claim.decision_reason = reason
        claim.decided_by, claim.decided_at = actor, now
        claim.save()
        item.transition_to(Item.Status.AWAITING_HANDOVER)
    audit.record(actor, "claim.approved", f"Claim {claim.reference} approved", target=claim, request=request,
                 competing_rejected=[o.reference for o in others], manual_review=bool(verified_by_staff),
                 legacy_quiz_override=override, legacy_quiz_result=legacy_result)
    notifications.notify([claim.claimant], "claim", "Claim approved",
                         f"Your claim {claim.reference} was approved. Security will schedule a supervised handover.",
                         link=f"/my/claims/{claim.pk}/")
    for other in others:
        notifications.notify([other.claimant], "claim", "Claim not approved",
                             f"Your claim {other.reference} was not approved.", link=f"/my/claims/{other.pk}/")
    return claim


def _release_approval(claim, item):
    handover = Handover.objects.filter(claim=claim, status=Handover.Status.SCHEDULED).first()
    if handover:
        handover.status = Handover.Status.CANCELLED
        handover.cancelled_reason = "Claim no longer approved."
        handover.otp_hash = ""
        handover.save()
    if item.status == Item.Status.AWAITING_HANDOVER:
        item.transition_to(Item.Status.IN_CUSTODY)


def reject_claim(claim_id, actor, reason, request=None):
    _require(actor, "decide_claims")
    if not (reason or "").strip():
        raise WorkflowError("A rejection reason is required.")
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        item = Item.objects.select_for_update().get(pk=claim.item_id)
        if claim.claimant_id == actor.pk:
            raise PermissionDenied("You cannot decide your own claim.")
        if claim.status not in Claim.ACTIVE_STATUSES:
            raise WorkflowError("This claim is already closed.")
        if claim.status == Claim.Status.APPROVED:
            _release_approval(claim, item)
        _cancel_open_challenge(claim)
        claim.status = Claim.Status.REJECTED
        claim.decision_reason = reason.strip()
        claim.decided_by, claim.decided_at = actor, timezone.now()
        claim.save()
    audit.record(actor, "claim.rejected", f"Claim {claim.reference} rejected", target=claim, request=request)
    notifications.notify([claim.claimant], "claim", "Claim not approved",
                         f"Your claim {claim.reference} was not approved. See the claim page for the reason.",
                         link=f"/my/claims/{claim.pk}/")
    return claim


def withdraw_claim(claim_id, user, request=None):
    with transaction.atomic():
        claim = Claim.objects.select_for_update().get(pk=claim_id)
        if claim.claimant_id != user.pk:
            raise PermissionDenied
        if claim.status not in Claim.ACTIVE_STATUSES:
            raise WorkflowError("This claim is already closed.")
        item = Item.objects.select_for_update().get(pk=claim.item_id)
        if claim.status == Claim.Status.APPROVED:
            _release_approval(claim, item)
        _cancel_open_challenge(claim)
        claim.status = Claim.Status.WITHDRAWN
        claim.save(update_fields=["status", "updated_at"])
    audit.record(user, "claim.withdrawn", f"Claim {claim.reference} withdrawn", target=claim, request=request)
    return claim
