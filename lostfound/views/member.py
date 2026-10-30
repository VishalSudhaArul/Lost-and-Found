"""Student & staff screens. Every object lookup is scoped to request.user,
so another user's report or claim returns 404 rather than leaking existence."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..forms import ClaimForm, ClaimReplyForm, ItemReportForm, PrivateDetailFormSet, VerificationForm
from ..models import Claim, Handover, Item, ItemCategory, MatchSuggestion, Notification, VerificationChallenge
from ..services import analytics, audit, claims, custody, matching, public, verification
from ..services.errors import WorkflowError


@login_required
def dashboard(request):
    return render(request, "member/dashboard.html", analytics.member_metrics(request.user))


def _category_attribute_map():
    data = {}
    for cat in ItemCategory.objects.filter(is_active=True).select_related("parent"):
        data[cat.pk] = [{"key": a.key, "label": a.label, "kind": a.kind, "choices": a.choice_list}
                        for a in cat.all_attributes()]
    return data


@login_required
def report_item(request, report_type):
    if report_type not in Item.ReportType.values:
        raise PermissionDenied
    is_found = report_type == Item.ReportType.FOUND
    form = ItemReportForm(request.POST or None, request.FILES or None, report_type=report_type)
    formset = PrivateDetailFormSet(request.POST or None, prefix="private") if is_found else None
    if request.method == "POST":
        valid = form.is_valid() and (formset.is_valid() if formset else True)
        if valid:
            with transaction.atomic():
                item = form.save(reporter=request.user)
                if formset:
                    for row in formset.cleaned_data:
                        if row and row.get("question") and row.get("answer"):
                            verification.add_private_detail(item, request.user, row["kind"], row["question"],
                                                            row["answer"], row["match_mode"])
                audit.record(request.user, "item.reported", f"{item.get_report_type_display()} report {item.reference}",
                             target=item, request=request)
            matching.refresh_for_item(item)
            messages.success(request, f"Report {item.reference} saved.")
            return redirect("report_detail", pk=item.pk)
    from ..services import rewards
    return render(request, "member/report_form.html", {
        "policy": rewards.policy(),
        "form": form, "formset": formset, "is_found": is_found, "report_type": report_type,
        "attribute_map": _category_attribute_map(),
        "attr_values_json": json.dumps({k[5:]: v for k, v in request.POST.items() if k.startswith("attr_")}),
    })


@login_required
def my_reports(request):
    reports = Item.objects.owned_by(request.user).select_related("category__parent", "location")
    status = request.GET.get("type")
    if status in Item.ReportType.values:
        reports = reports.filter(report_type=status)
    return render(request, "member/my_reports.html", {"reports": reports, "type": status})


@login_required
def report_detail(request, pk):
    item = get_object_or_404(Item.objects.select_related("category__parent", "location"), pk=pk, reporter=request.user)
    context = {"item": item}
    if item.is_found:
        context["claim_count"] = Claim.objects.filter(item=item, status__in=Claim.ACTIVE_STATUSES).count()
        from ..models import PrivateDetail
        context["private_count"] = PrivateDetail.vault.for_item(item).count()
    else:
        context["match_count"] = MatchSuggestion.objects.filter(lost_item=item, status=MatchSuggestion.Status.SUGGESTED).count()
    return render(request, "member/report_detail.html", context)


@login_required
@require_POST
def close_report(request, pk):
    item = get_object_or_404(Item, pk=pk, reporter=request.user)
    if item.status != Item.Status.OPEN:
        messages.error(request, "Only open reports can be closed.")
    elif item.is_found and Claim.objects.filter(item=item, status__in=Claim.ACTIVE_STATUSES).exists():
        messages.error(request, "This item has an active claim; campus security must resolve it first.")
    else:
        item.transition_to(Item.Status.CLOSED)
        matching.refresh_for_item(item)
        audit.record(request.user, "item.closed", f"{item.reference} closed by reporter", target=item, request=request)
        messages.success(request, f"{item.reference} closed.")
    return redirect("report_detail", pk=item.pk)


@login_required
def report_matches(request, pk):
    item = get_object_or_404(Item, pk=pk, reporter=request.user, report_type=Item.ReportType.LOST)
    if request.method == "POST":
        MatchSuggestion.objects.filter(pk=request.POST.get("dismiss"), lost_item=item).update(
            status=MatchSuggestion.Status.DISMISSED)
        messages.info(request, "Suggestion dismissed.")
        return redirect("report_matches", pk=item.pk)
    suggestions = []
    for s in MatchSuggestion.objects.filter(lost_item=item).exclude(status=MatchSuggestion.Status.DISMISSED):
        card = public.summary(s.found_item_id)
        if card:
            suggestions.append({"suggestion": s, "card": card, "factors": matching.explain(s)})
    return render(request, "member/matches.html", {"item": item, "suggestions": suggestions})


@login_required
def claim_create(request, item_pk):
    item = get_object_or_404(Item, pk=item_pk, report_type=Item.ReportType.FOUND)
    card = public.public_item(item.pk)
    ok, reason = claims.can_claim(item, request.user)
    if card is None or not ok:
        messages.error(request, reason or "This item is not open for claims.")
        return redirect("board")
    form = ClaimForm(request.POST or None, request.FILES or None, user=request.user, initial={"lost_report": request.GET.get("lost")})
    if request.method == "POST" and form.is_valid():
        try:
            d = form.cleaned_data
            claim = claims.submit_claim(item, request.user, d["statement"], d.get("lost_report"), request=request,
                                        where_when=d["where_when"], lost_when=d["lost_when"],
                                        extra_info=d.get("extra_info", ""),
                                        attachment=d.get("attachment"))
        except WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Your claim has been submitted successfully. Campus security will review "
                                      "your information and contact you.")
            return redirect("claim_detail", pk=claim.pk)
    return render(request, "member/claim_form.html", {"form": form, "card": card})


@login_required
def my_claims(request):
    rows = Claim.objects.filter(claimant=request.user).select_related("item")
    return render(request, "member/my_claims.html", {"claims": rows})


@login_required
def claim_detail(request, pk):
    claim = get_object_or_404(Claim.objects.select_related("item"), pk=pk, claimant=request.user)
    challenge = VerificationChallenge.objects.filter(claim=claim).first()
    handover = Handover.objects.filter(claim=claim).select_related("location").first()
    return render(request, "member/claim_detail.html", {
        "claim": claim, "card": public.summary(claim.item_id), "challenge": challenge, "handover": handover,
        "thread": claim.messages.all(), "reply_form": ClaimReplyForm()})


@login_required
@require_POST
def claim_respond(request, pk):
    claim = get_object_or_404(Claim, pk=pk, claimant=request.user)
    form = ClaimReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Write a short answer.")
    else:
        try:
            claims.respond_to_request(claim.pk, request.user, form.cleaned_data["message"], request=request)
        except WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Thanks, your answer was sent to campus security.")
    return redirect("claim_detail", pk=claim.pk)


@login_required
@never_cache
def claim_verify(request, pk):
    claim = get_object_or_404(Claim, pk=pk, claimant=request.user)
    challenge = VerificationChallenge.objects.filter(claim=claim).first()
    if challenge is None or challenge.status != VerificationChallenge.Status.OPEN or claim.status != Claim.Status.PENDING_VERIFICATION:
        return redirect("claim_detail", pk=claim.pk)
    questions = verification.questions_for_claimant(challenge)
    form = VerificationForm(request.POST or None, questions=questions)
    if request.method == "POST" and form.is_valid():
        try:
            result = verification.submit_attempt(challenge.pk, request.user, form.cleaned_data, request=request)
        except WorkflowError as exc:
            messages.error(request, str(exc))
            return redirect("claim_detail", pk=claim.pk)
        if result["passed"]:
            messages.success(request, "Thank you. Your answers were accepted and sent to campus security for review.")
            return redirect("claim_detail", pk=claim.pk)
        if result["locked"]:
            messages.error(request, "Your answers did not match and no attempts remain. Campus security will review the claim.")
            return redirect("claim_detail", pk=claim.pk)
        messages.error(request, f"Your answers did not match the finder's record. "
                                f"{result['attempts_remaining']} attempt(s) remaining.")
        return redirect("claim_verify", pk=claim.pk)
    return render(request, "member/verify.html", {"claim": claim, "challenge": challenge, "form": form})


@login_required
@require_POST
def claim_withdraw(request, pk):
    claim = get_object_or_404(Claim, pk=pk, claimant=request.user)
    try:
        claims.withdraw_claim(claim.pk, request.user, request=request)
        messages.success(request, f"Claim {claim.reference} withdrawn.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
    return redirect("claim_detail", pk=claim.pk)


@login_required
@require_POST
@never_cache
def handover_code(request, pk):
    claim = get_object_or_404(Claim, pk=pk, claimant=request.user)
    handover = Handover.objects.filter(claim=claim).first()
    if handover is None:
        raise PermissionDenied
    try:
        code, expires = custody.issue_otp(handover.pk, request.user, request=request)
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("claim_detail", pk=claim.pk)
    response = render(request, "member/handover_code.html", {"claim": claim, "code": code, "expires": expires})
    response["Cache-Control"] = "no-store"
    return response


@login_required
def notifications_view(request):
    if request.method == "POST":
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return redirect("notifications")
    items = Notification.objects.filter(user=request.user)[:100]
    return render(request, "member/notifications.html", {"notifications": items})


@login_required
def notification_open(request, pk):
    note = get_object_or_404(Notification, pk=pk, user=request.user)
    if not note.is_read:
        note.is_read = True
        note.save(update_fields=["is_read"])
    if note.link and url_has_allowed_host_and_scheme(note.link, allowed_hosts={request.get_host()}):
        return redirect(note.link)
    return redirect("notifications")
