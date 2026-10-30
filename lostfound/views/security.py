"""Security personnel screens. Every view is guarded by a permission
decorator and every state change goes through a service that re-checks it."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (ApproveForm, CollectionCodeForm, RequestInfoForm, CompleteHandoverForm, CorrectionForm, DisposalForm, ExtendRetentionForm,
                     PrivateDetailForm, ReceiveItemForm, RejectForm, ScheduleHandoverForm, TransferForm)
from ..models import Claim, CustodyRecord, DisposalRecord, Handover, Item, PrivateDetail
from ..permissions import any_perm_required, perm_required
from ..services import analytics, claims, custody, disposal, verification
from ..services.errors import WorkflowError


def _flash(request, fn, /, *args, success=None, **kwargs):
    try:
        result = fn(*args, **kwargs)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return None
    if success:
        messages.success(request, success)
    return result


@perm_required("access_security_dashboard")
def dashboard(request):
    return render(request, "security/dashboard.html", analytics.security_metrics())


@any_perm_required("manage_custody", "review_claims")
def items(request):
    qs = Item.objects.found().select_related("category__parent", "location", "current_custody_location", "reporter")
    state = request.GET.get("state", "intake")
    states = {
        "intake": [Item.Status.OPEN],
        "custody": [Item.Status.IN_CUSTODY],
        "claims": [Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW],
        "handover": [Item.Status.AWAITING_HANDOVER],
        "review": [Item.Status.DISPOSAL_REVIEW],
        "closed": [Item.Status.RETURNED, Item.Status.DISPOSED, Item.Status.CLOSED],
    }
    pending = Claim.objects.filter(status__in=[Claim.Status.PENDING_VERIFICATION, Claim.Status.UNDER_REVIEW,
                                               Claim.Status.INFO_REQUESTED]).values("item_id")
    qs = qs.filter(status__in=states.get(state, states["intake"]))
    if state == "claims":
        qs = qs.filter(pk__in=pending)
    elif state == "custody":
        qs = qs.exclude(pk__in=pending)
    counts = {
        "intake": Item.objects.found().filter(status=Item.Status.OPEN).count(),
        "custody": Item.objects.found().filter(status=Item.Status.IN_CUSTODY).exclude(pk__in=pending).count(),
        "claims": Item.objects.found().filter(status__in=states["claims"], pk__in=pending).count(),
        "handover": Item.objects.found().filter(status=Item.Status.AWAITING_HANDOVER).count(),
        "review": Item.objects.found().filter(status=Item.Status.DISPOSAL_REVIEW).count(),
    }
    q = request.GET.get("q", "").strip()
    if q:
        ref = q.upper().lstrip("F-").lstrip("0")
        qs = qs.filter(Q(title__icontains=q) | Q(pk=int(ref)) if ref.isdigit() else Q(title__icontains=q))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "security/items.html", {"page": page, "state": state, "q": q, "counts": counts})


@any_perm_required("manage_custody", "review_claims")
def item_detail(request, pk):
    item = get_object_or_404(Item.objects.select_related("category__parent", "location", "reporter",
                                                         "current_custody_location"), pk=pk)
    item_claims = list(Claim.objects.filter(item=item).select_related("claimant"))
    open_claims = [c for c in item_claims if c.status in (Claim.Status.PENDING_VERIFICATION, Claim.Status.UNDER_REVIEW,
                                                          Claim.Status.INFO_REQUESTED)]
    approved = next((c for c in item_claims if c.status in (Claim.Status.APPROVED, Claim.Status.COMPLETED)), None)
    handover = Handover.objects.filter(claim=approved).select_related("location", "officer", "completed_by").first() \
        if approved else None
    context = {
        "item": item, "open_claims": open_claims, "approved": approved, "handover": handover,
        "records": CustodyRecord.objects.filter(item=item).select_related("from_location", "to_location", "accepted_by", "corrects"),
        "claims": item_claims,
        "private_count": PrivateDetail.vault.for_item(item).count(),
        "receive_form": ReceiveItemForm(),
        "transfer_form": TransferForm(),
        "detail_form": PrivateDetailForm(),
        "correction_form": CorrectionForm(),
    }
    return render(request, "security/item_detail.html", context)


@perm_required("manage_custody")
@require_POST
def item_receive(request, pk):
    form = ReceiveItemForm(request.POST)
    if form.is_valid():
        _flash(request, custody.receive_item, pk, request.user, form.cleaned_data["location"],
               form.cleaned_data["received_from"], form.cleaned_data["notes"], request=request,
               success="Item received into custody.")
    else:
        messages.error(request, "Choose a custody point.")
    return redirect("security_item", pk=pk)


@perm_required("manage_custody")
@require_POST
def item_transfer(request, pk):
    form = TransferForm(request.POST)
    if form.is_valid():
        _flash(request, custody.transfer_item, pk, request.user, form.cleaned_data["location"], form.cleaned_data["notes"],
               request=request, success="Transfer recorded.")
    else:
        messages.error(request, "Choose a custody point.")
    return redirect("security_item", pk=pk)


@perm_required("manage_custody")
@require_POST
def custody_correct(request, pk):
    form = CorrectionForm(request.POST)
    if form.is_valid():
        record = get_object_or_404(CustodyRecord, pk=form.cleaned_data["record_id"], item_id=pk)
        _flash(request, custody.correct_record, record.pk, request.user, form.cleaned_data["notes"], request=request,
               success="Correction appended to the custody history.")
    else:
        messages.error(request, "Describe the correction.")
    return redirect("security_item", pk=pk)


@perm_required("manage_custody")
@require_POST
def private_detail_add(request, pk):
    item = get_object_or_404(Item, pk=pk, report_type=Item.ReportType.FOUND)
    if item.status in Item.TERMINAL_STATUSES:
        raise PermissionDenied
    form = PrivateDetailForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        verification.add_private_detail(item, request.user, d["kind"], d["question"], d["answer"], d["match_mode"])
        issued = verification.issue_pending_challenges(item)
        messages.success(request, "Private detail recorded." + (f" {len(issued)} waiting claim(s) can now be verified." if issued else ""))
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("security_item", pk=pk)


@perm_required("review_claims")
def claims_queue(request):
    tab = request.GET.get("tab", "review")
    statuses = {"review": [Claim.Status.UNDER_REVIEW],
                "pending": [Claim.Status.PENDING_VERIFICATION, Claim.Status.INFO_REQUESTED],
                "approved": [Claim.Status.APPROVED], "closed": [Claim.Status.REJECTED, Claim.Status.WITHDRAWN, Claim.Status.COMPLETED]}
    qs = Claim.objects.filter(status__in=statuses.get(tab, statuses["review"])).select_related("item", "claimant")
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "security/claims.html", {"page": page, "tab": tab})


@perm_required("review_claims", "view_private_details")
def claim_review(request, pk):
    claim = get_object_or_404(Claim.objects.select_related("item__category__parent", "item__location", "claimant",
                                                           "lost_report", "decided_by"), pk=pk)
    competing = Claim.objects.filter(item=claim.item, status__in=Claim.ACTIVE_STATUSES).exclude(pk=claim.pk).select_related("claimant")
    handover = Handover.objects.filter(claim=claim).select_related("location", "officer").first()
    context = {
        "claim": claim, "competing": competing, "handover": handover,
        "evidence": verification.review_data(claim, request.user, request=request),
        "approve_form": ApproveForm(), "reject_form": RejectForm(), "info_form": RequestInfoForm(),
        "thread": claim.messages.select_related("author"),
        "schedule_form": ScheduleHandoverForm(initial={"officer": request.user.pk}),
    }
    return render(request, "security/claim_review.html", context)


@perm_required("decide_claims")
@require_POST
def claim_approve(request, pk):
    form = ApproveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Tick the in-person verification box and give a reason for the decision.")
        return redirect("security_claim", pk=pk)
    _flash(request, claims.approve_claim, pk, request.user, form.cleaned_data.get("reason", ""),
           form.cleaned_data.get("confirm_competing", False), request=request, verified_in_person=True,
           success="Claim approved.")
    return redirect("security_claim", pk=pk)


@perm_required("review_claims")
@require_POST
def claim_request_info(request, pk):
    form = RequestInfoForm(request.POST)
    if form.is_valid():
        _flash(request, claims.request_info, pk, request.user, form.cleaned_data["message"], request=request,
               success="Question sent to the claimant.")
    else:
        messages.error(request, "Write the question for the claimant.")
    return redirect("security_claim", pk=pk)


@perm_required("decide_claims")
@require_POST
def claim_reject(request, pk):
    form = RejectForm(request.POST)
    if form.is_valid():
        _flash(request, claims.reject_claim, pk, request.user, form.cleaned_data["reason"], request=request,
               success="Claim rejected.")
    else:
        messages.error(request, "A rejection reason is required.")
    return redirect("security_claim", pk=pk)


@perm_required("conduct_handover")
def handovers(request):
    scheduled = Handover.objects.filter(status=Handover.Status.SCHEDULED).select_related("claim__item", "claim__claimant", "location", "officer")
    recent = Handover.objects.exclude(status=Handover.Status.SCHEDULED).select_related("claim__item", "claim__claimant").order_by("-updated_at")[:20]
    return render(request, "security/handovers.html", {"scheduled": scheduled, "recent": recent})


@perm_required("conduct_handover")
@require_POST
def handover_schedule(request, claim_pk):
    form = ScheduleHandoverForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        handover = _flash(request, custody.schedule_handover, claim_pk, request.user, d["scheduled_for"], d["location"],
                          d["officer"], d["method"], request=request, success="Handover scheduled and claimant notified.")
        if handover:
            return redirect("security_handover", pk=handover.pk)
    else:
        messages.error(request, "Check the handover details: " + " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("security_claim", pk=claim_pk)


@perm_required("conduct_handover")
def handover_detail(request, pk):
    handover = get_object_or_404(Handover.objects.select_related("claim__item", "claim__claimant", "location", "officer",
                                                                 "completed_by"), pk=pk)
    action = request.POST.get("action", "complete") if request.method == "POST" else ""
    code_form = CollectionCodeForm(request.POST if action == "verify" else None)
    if action == "send_code":
        _flash(request, custody.email_collection_code, handover.pk, request.user, request=request,
               success=f"A new collection code was emailed to {handover.claim.claimant.get_full_name() or handover.claim.claimant.username}.")
        return redirect("security_handover", pk=handover.pk)
    if action == "verify":
        if code_form.is_valid():
            try:
                ok, msg = custody.verify_collection_code(handover.pk, request.user, code_form.cleaned_data["code"],
                                                         request=request)
            except WorkflowError as exc:
                ok, msg = False, str(exc)
            (messages.success if ok else messages.error)(request, msg)
            return redirect("security_handover", pk=handover.pk)
        messages.error(request, "Enter the 6-digit collection code.")
    form = CompleteHandoverForm(request.POST if action == "complete" else None,
                                request.FILES if action == "complete" else None)
    if action == "complete" and form.is_valid():
        d = form.cleaned_data
        try:
            ok, msg = custody.complete_handover(
                handover.pk, request.user, id_type=d["id_type"], id_name_matches=d["id_name_matches"],
                otp_code=d.get("otp_code", ""), signature_data=d.get("signature_data", ""),
                proof_photo=d.get("proof_photo"), notes=d.get("notes", ""), request=request)
        except WorkflowError as exc:
            ok, msg = False, str(exc)
        (messages.success if ok else messages.error)(request, msg)
        return redirect("security_handover", pk=handover.pk)
    return render(request, "security/handover_detail.html", {
        "handover": handover, "form": form, "code_form": code_form, "blockers": custody.handover_blockers(handover),
        "code_active": bool(handover.otp_hash and not handover.otp_used_at and handover.otp_expires_at
                            and handover.otp_expires_at > timezone.now())})


@perm_required("conduct_handover")
@require_POST
def handover_cancel(request, pk):
    _flash(request, custody.cancel_handover, pk, request.user, request.POST.get("reason", ""), request=request,
           success="Handover cancelled.")
    return redirect("security_handover", pk=pk)


@perm_required("manage_custody")
def custody_history(request):
    qs = CustodyRecord.objects.select_related("item", "from_location", "to_location", "accepted_by").order_by("-created_at", "-id")
    action = request.GET.get("action")
    if action in CustodyRecord.Action.values:
        qs = qs.filter(action=action)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "security/custody_history.html", {"page": page, "action": action,
                                                              "actions": CustodyRecord.Action.choices})


@any_perm_required("review_disposal", "authorize_disposal")
def ageing(request):
    return render(request, "security/ageing.html", {
        "rows": disposal.ageing_queue(), "disposals": DisposalRecord.objects.select_related("item", "authorized_by", "reviewed_by")
        .order_by("-disposed_at")[:20],
        "disposal_form": DisposalForm(), "extend_form": ExtendRetentionForm()})


@perm_required("review_disposal")
@require_POST
def ageing_review(request, pk):
    _flash(request, disposal.confirm_review, pk, request.user, request=request, success="Review recorded.")
    return redirect("security_ageing")


@perm_required("review_disposal")
@require_POST
def ageing_extend(request, pk):
    form = ExtendRetentionForm(request.POST)
    if form.is_valid():
        _flash(request, disposal.extend_retention, pk, request.user, form.cleaned_data["days"], form.cleaned_data["reason"],
               request=request, success="Retention extended.")
    else:
        messages.error(request, "Enter a number of days and a reason.")
    return redirect("security_ageing")


@perm_required("authorize_disposal")
@require_POST
def ageing_dispose(request, pk):
    form = DisposalForm(request.POST, request.FILES)
    if form.is_valid():
        d = form.cleaned_data
        _flash(request, disposal.dispose, pk, request.user, d["method"], d["reason"], d["recipient"], d.get("evidence"),
               request=request, success="Disposal recorded.")
    else:
        messages.error(request, "Choose a method and give a reason.")
    return redirect("security_ageing")


PRIVATE_FILES = {
    "signature": (Handover, "signature_image", "conduct_handover"),
    "proof": (Handover, "proof_photo", "conduct_handover"),
    "disposal": (DisposalRecord, "evidence", "review_disposal"),
    "claim": (Claim, "attachment", "review_claims"),
}


@any_perm_required("conduct_handover", "review_disposal", "review_claims")
def private_file(request, kind, pk):
    if kind not in PRIVATE_FILES:
        raise Http404
    model, field, perm = PRIVATE_FILES[kind]
    if not request.user.has_perm(f"lostfound.{perm}"):
        raise PermissionDenied
    obj = get_object_or_404(model, pk=pk)
    f = getattr(obj, field)
    if not f:
        raise Http404
    response = FileResponse(f.open("rb"), content_type="image/png" if f.name.endswith(".png") else "image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = "inline"
    return response
