"""Lost & Found team: verify or reject new lost and found reports."""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..forms import ReportRejectForm, ReportVerifyForm
from ..models import Item, MatchSuggestion, PrivateDetail
from ..permissions import perm_required
from ..services import reports
from ..services.errors import WorkflowError

TABS = {"pending": Item.Review.PENDING, "verified": Item.Review.VERIFIED, "rejected": Item.Review.REJECTED}


@perm_required("review_reports")
def queue(request):
    tab = request.GET.get("tab", "pending")
    kind = request.GET.get("type", "")
    qs = Item.objects.select_related("category__parent", "location", "reporter", "reviewed_by")
    qs = qs.filter(review_status=TABS.get(tab, Item.Review.PENDING))
    if kind in Item.ReportType.values:
        qs = qs.filter(report_type=kind)
    qs = qs.order_by("created_at" if tab == "pending" else "-reviewed_at")
    counts = Item.objects.aggregate(**{t: Count("pk", filter=Q(review_status=v)) for t, v in TABS.items()})
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "security/reports_queue.html", {
        "page": page, "tab": tab, "type": kind, "counts": counts,
        "querystring": f"tab={tab}&type={kind}"})


@perm_required("review_reports")
def review(request, pk):
    item = get_object_or_404(Item.objects.select_related("category__parent", "location", "reporter", "reviewed_by"), pk=pk)
    duplicates = (Item.objects.filter(report_type=item.report_type, category=item.category, reporter=item.reporter)
                  .exclude(pk=item.pk).filter(created_at__gte=item.created_at - timedelta(days=7))[:5])
    context = {
        "item": item, "verify_form": ReportVerifyForm(), "reject_form": ReportRejectForm(),
        "private_count": PrivateDetail.vault.for_item(item).count() if item.is_found else None,
        "match_count": MatchSuggestion.objects.filter(Q(lost_item=item) | Q(found_item=item)).count(),
        "duplicates": duplicates, "own_report": item.reporter_id == request.user.pk,
    }
    return render(request, "security/report_review.html", context)


@perm_required("review_reports")
@require_POST
def verify(request, pk):
    form = ReportVerifyForm(request.POST)
    if form.is_valid():
        try:
            item = reports.verify(pk, request.user, form.cleaned_data["note"], form.cleaned_data["quality_bonus"],
                                  request=request)
            messages.success(request, f"{item.reference} verified. The reporter has been notified.")
        except WorkflowError as exc:
            messages.error(request, str(exc))
            return redirect("security_report", pk=pk)
    return redirect(_queue_url())


@perm_required("review_reports")
@require_POST
def reject(request, pk):
    form = ReportRejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give the reporter a short reason (at least 5 characters).")
        return redirect("security_report", pk=pk)
    try:
        item = reports.reject(pk, request.user, form.cleaned_data["reason"], request=request)
        messages.success(request, f"{item.reference} rejected and hidden from the board.")
    except WorkflowError as exc:
        messages.error(request, str(exc))
        return redirect("security_report", pk=pk)
    return redirect(_queue_url())


def _queue_url():
    return reverse("security_reports")
