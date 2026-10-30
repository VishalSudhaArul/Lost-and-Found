"""M6 — custody chain and supervised handover."""
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import Claim, CustodyRecord, Handover, Item, Location, MatchSuggestion, OperationalSettings
from . import audit, images, matching, notifications, ratelimit
from .errors import WorkflowError


def _require(user, perm):
    if not user.has_perm(f"lostfound.{perm}"):
        raise PermissionDenied


def custody_points():
    return Location.objects.filter(is_custody_point=True, is_active=True)


def receive_item(item_id, actor, custody_location, received_from="", notes="", request=None):
    _require(actor, "manage_custody")
    if not custody_location.is_custody_point or not custody_location.is_active:
        raise WorkflowError("Choose an active custody point (security office or locker).")
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if not item.is_found or item.status != Item.Status.OPEN:
            raise WorkflowError("Only newly reported found items can be received into custody.")
        record = CustodyRecord.objects.create(
            item=item, action=CustodyRecord.Action.RECEIVED, to_location=custody_location,
            received_from=received_from or item.reporter.get_full_name() or item.reporter.username,
            accepted_by=actor, notes=notes)
        item.transition_to(Item.Status.IN_CUSTODY, save=False)
        item.custody_since = record.created_at
        item.current_custody_location = custody_location
        item.save()
    audit.record(actor, "custody.received", f"{item.reference} received at {custody_location}", target=item, request=request)
    from . import reports, rewards
    if item.review_status == Item.Review.PENDING:
        reports.verify(item.pk, actor, note="Verified on receipt into custody.", request=request, auto=True)
        item.refresh_from_db()
    rewards.on_custody_received(item, actor)
    notifications.notify([item.reporter], "handover", "Item received by security",
                         f"Thank you — {item.reference} is now held by campus security.", link=f"/my/reports/{item.pk}/")
    matching.refresh_for_item(item)
    return record


def transfer_item(item_id, actor, to_location, notes="", request=None):
    _require(actor, "manage_custody")
    if not to_location.is_custody_point or not to_location.is_active:
        raise WorkflowError("Items can only be transferred to an active custody point.")
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_id)
        if item.status not in (Item.Status.IN_CUSTODY, Item.Status.AWAITING_HANDOVER, Item.Status.DISPOSAL_REVIEW):
            raise WorkflowError("This item is not in custody.")
        if item.current_custody_location_id == to_location.pk:
            raise WorkflowError("The item is already at that location.")
        record = CustodyRecord.objects.create(
            item=item, action=CustodyRecord.Action.TRANSFERRED, from_location=item.current_custody_location,
            to_location=to_location, accepted_by=actor, notes=notes)
        item.current_custody_location = to_location
        item.save(update_fields=["current_custody_location", "updated_at"])
    audit.record(actor, "custody.transferred", f"{item.reference} moved to {to_location}", target=item, request=request)
    return record


def correct_record(record_id, actor, notes, request=None):
    """Corrections never edit history: they append a CORRECTION entry."""
    _require(actor, "manage_custody")
    if not (notes or "").strip():
        raise WorkflowError("Describe what was wrong in the original entry.")
    original = CustodyRecord.objects.get(pk=record_id)
    record = CustodyRecord.objects.create(item=original.item, action=CustodyRecord.Action.CORRECTION,
                                          accepted_by=actor, notes=notes.strip()[:500], corrects=original)
    audit.record(actor, "custody.corrected", f"Correction to custody entry #{original.pk}", target=original.item, request=request)
    return record


def schedule_handover(claim_id, actor, scheduled_for, location, officer, method=Handover.Method.OTP, request=None):
    _require(actor, "conduct_handover")
    if not officer.has_perm("lostfound.conduct_handover"):
        raise WorkflowError("The assigned officer is not authorised to conduct handovers.")
    if not location.is_custody_point:
        raise WorkflowError("Handovers take place at a custody point.")
    if scheduled_for < timezone.now() - timezone.timedelta(minutes=5):
        raise WorkflowError("Choose a future time.")
    with transaction.atomic():
        claim = Claim.objects.select_for_update().select_related("item").get(pk=claim_id)
        if claim.status != Claim.Status.APPROVED:
            raise WorkflowError("Only approved claims can be scheduled for handover.")
        if claim.claimant_id == officer.pk:
            raise WorkflowError("The claimant cannot supervise their own handover.")
        handover = Handover.objects.select_for_update().filter(claim=claim).first()
        if handover and handover.status == Handover.Status.COMPLETED:
            raise WorkflowError("This handover is already complete.")
        if handover is None:
            handover = Handover(claim=claim)
        handover.location, handover.scheduled_for, handover.officer = location, scheduled_for, officer
        handover.scheduled_by, handover.method, handover.status = actor, method, Handover.Status.SCHEDULED
        handover.otp_hash, handover.otp_expires_at, handover.otp_attempts = "", None, 0
        handover.cancelled_reason = ""
        handover.save()
    when = timezone.localtime(scheduled_for).strftime("%d %b %Y, %H:%M")
    audit.record(actor, "handover.scheduled", f"Handover for {claim.reference} scheduled {when}", target=handover, request=request)
    notifications.notify([claim.claimant], "handover", "Handover scheduled",
                         f"Collect {claim.item.reference} at {location.name} on {when}. Bring your university ID.",
                         link=f"/my/claims/{claim.pk}/", dedupe_key=f"handover:{handover.pk}:{scheduled_for.isoformat()}")
    return handover


def cancel_handover(handover_id, actor, reason, request=None):
    _require(actor, "conduct_handover")
    with transaction.atomic():
        handover = Handover.objects.select_for_update().get(pk=handover_id)
        if handover.status != Handover.Status.SCHEDULED:
            raise WorkflowError("Only scheduled handovers can be cancelled.")
        handover.status = Handover.Status.CANCELLED
        handover.cancelled_reason = (reason or "")[:300]
        handover.otp_hash = ""
        handover.save()
    audit.record(actor, "handover.cancelled", f"Handover {handover.receipt_code} cancelled", target=handover, request=request)
    notifications.notify([handover.claim.claimant], "handover", "Handover cancelled",
                         "Your handover appointment was cancelled. Security will contact you to reschedule.",
                         link=f"/my/claims/{handover.claim_id}/")
    return handover


def issue_otp(handover_id, user, request=None):
    """Generate a fresh 6-digit code for the claimant. Only a salted hash is
    stored; the plaintext is returned once for display and never logged."""
    limit, window = settings.OTP_REVEAL_RATE_LIMIT
    try:
        ratelimit.hit("otp", user.pk, limit, window)
    except ratelimit.RateLimited:
        raise WorkflowError("Too many codes requested. Please wait before requesting another.")
    cfg = OperationalSettings.load()
    with transaction.atomic():
        handover = Handover.objects.select_for_update().get(pk=handover_id)
        if handover.claim.claimant_id != user.pk:
            raise PermissionDenied
        if handover.status != Handover.Status.SCHEDULED or handover.method != Handover.Method.OTP:
            raise WorkflowError("A handover code is not available for this appointment.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        handover.otp_hash = make_password(code)
        handover.otp_expires_at = timezone.now() + timezone.timedelta(minutes=cfg.otp_ttl_minutes)
        handover.otp_attempts = 0
        handover.otp_used_at = None
        handover.save(update_fields=["otp_hash", "otp_expires_at", "otp_attempts", "otp_used_at", "updated_at"])
    audit.record(user, "handover.code_issued", f"Handover code issued for {handover.receipt_code}", target=handover, request=request)
    return code, handover.otp_expires_at


def handover_blockers(handover):
    """Reasons a handover cannot complete (empty list = ready)."""
    claim, item = handover.claim, handover.claim.item
    reasons = []
    if handover.status != Handover.Status.SCHEDULED:
        reasons.append("The handover is not scheduled.")
    if claim.status != Claim.Status.APPROVED:
        reasons.append("The claim is not approved.")
    # PASSED is set only when an authorised officer approves after comparing the evidence
    # (or, for legacy claims, by the old quiz). The quiz itself is no longer required.
    if claim.verification_status != Claim.Verification.PASSED:
        reasons.append("Ownership verification has not been passed.")
    if item.status != Item.Status.AWAITING_HANDOVER:
        reasons.append("The item is not awaiting handover.")
    return reasons


def complete_handover(handover_id, actor, *, id_type, id_name_matches, otp_code="", signature_data="",
                      proof_photo=None, notes="", request=None):
    """Returns (ok: bool, message: str). Failed OTP attempts are committed."""
    _require(actor, "conduct_handover")
    cfg = OperationalSettings.load()
    with transaction.atomic():
        handover = Handover.objects.select_for_update().get(pk=handover_id)
        claim = Claim.objects.select_for_update().select_related("item").get(pk=handover.claim_id)
        if claim.claimant_id == actor.pk:
            raise PermissionDenied("You cannot complete a handover to yourself.")
        blockers = handover_blockers(handover)
        if blockers:
            raise WorkflowError(" ".join(blockers))
        if not id_name_matches or id_type not in Handover.IdType.values:
            raise WorkflowError("Confirm that you checked the claimant's photo ID and that the name matches the account.")

        signature_file = None
        if handover.method == Handover.Method.OTP:
            now = timezone.now()
            if not handover.otp_hash or handover.otp_used_at:
                return False, "The claimant has not generated a handover code yet."
            if handover.otp_attempts >= cfg.otp_max_attempts:
                return False, "Too many incorrect codes. Ask the claimant to generate a new code."
            if handover.otp_expires_at is None or handover.otp_expires_at < now:
                return False, "The handover code has expired. Ask the claimant to generate a new one."
            if not check_password((otp_code or "").strip(), handover.otp_hash):
                handover.otp_attempts += 1
                handover.save(update_fields=["otp_attempts", "updated_at"])
                audit.record(actor, "handover.code_rejected", f"Incorrect code for {handover.receipt_code}", target=handover,
                             request=request, attempt=handover.otp_attempts)
                return False, "The code is incorrect."
        else:
            try:
                signature_file = images.signature_from_data_url(signature_data)
            except ValidationError as exc:
                raise WorkflowError(" ".join(exc.messages))

        proof_file = None
        if proof_photo:
            try:
                proof_file = images.process_upload(proof_photo)
            except ValidationError as exc:
                raise WorkflowError(" ".join(exc.messages))

        now = timezone.now()
        item = Item.objects.select_for_update().get(pk=claim.item_id)
        if signature_file:
            handover.signature_image.save(signature_file.name, signature_file, save=False)
        if proof_file:
            handover.proof_photo.save(proof_file.name, proof_file, save=False)
        handover.id_type_checked, handover.id_name_matches = id_type, True
        handover.status, handover.completed_by, handover.completed_at = Handover.Status.COMPLETED, actor, now
        if handover.method == Handover.Method.OTP:
            handover.otp_used_at = now
        handover.otp_hash = ""
        handover.notes = (notes or "")[:500]
        handover.save()

        claim.status = Claim.Status.COMPLETED
        claim.save(update_fields=["status", "updated_at"])
        CustodyRecord.objects.create(item=item, action=CustodyRecord.Action.RELEASED,
                                     from_location=item.current_custody_location, accepted_by=actor,
                                     notes=f"Released to verified claimant under receipt {handover.receipt_code}.")
        item.transition_to(Item.Status.RETURNED, save=False)
        item.current_custody_location = None
        item.save()
        if claim.lost_report_id:
            lost = Item.objects.select_for_update().get(pk=claim.lost_report_id)
            if lost.status == Item.Status.OPEN:
                lost.transition_to(Item.Status.RETURNED)
        MatchSuggestion.objects.filter(found_item=item, status=MatchSuggestion.Status.SUGGESTED).delete()
        # Finder's return bonus is part of the same transaction; its unique ledger
        # reference ("return:<item>") makes a repeated completion award nothing.
        from . import rewards
        rewards.on_handover_completed(handover)
    audit.record(actor, "handover.completed", f"{item.reference} handed over (receipt {handover.receipt_code})",
                 target=handover, request=request, method=handover.method, id_type=id_type)
    notifications.notify([claim.claimant], "handover", "Item returned",
                         f"{item.reference} was handed over to you. Receipt {handover.receipt_code}.", link=f"/my/claims/{claim.pk}/")
    notifications.notify([item.reporter], "handover", "The item you found was returned",
                         f"{item.reference} has been returned to its owner. Thank you!", link=f"/my/reports/{item.pk}/")
    return True, f"Handover complete. Receipt {handover.receipt_code}."
