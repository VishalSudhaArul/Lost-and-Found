"""Dashboard and report metrics, computed from live database rows.

Definitions (also shown in the UI):
* Return rate      = found reports now RETURNED / all found reports (excluding withdrawn).
* Time to return   = handover completion − found-report creation (median, hours).
* Verification failure rate = failed attempts / all verification attempts.
* Hotspots         = lost reports per location and per hour-of-day of ``occurred_at``.
"""
import statistics
from collections import Counter

from django.db.models import Count
from django.utils import timezone

from ..models import Claim, DisposalRecord, Handover, Item, MatchSuggestion, Notification, VerificationAttempt
from . import disposal


def _bars(pairs, limit=8):
    rows = list(pairs)[:limit]
    peak = max((c for _, c in rows), default=0) or 1
    return [{"label": label, "count": count, "pct": round(count * 100 / peak)} for label, count in rows]


def admin_metrics():
    found = Item.objects.found().exclude(status=Item.Status.CLOSED)
    lost = Item.objects.lost()
    found_total = found.count()
    returned = found.filter(status=Item.Status.RETURNED).count()
    durations = [
        (h.completed_at - h.claim.item.created_at).total_seconds() / 3600
        for h in Handover.objects.filter(status=Handover.Status.COMPLETED).select_related("claim__item")
    ]
    attempts = VerificationAttempt.objects.count()
    failed = VerificationAttempt.objects.filter(passed=False).count()

    cat_counts = Counter()
    for row in Item.objects.values("category__name", "category__parent__name").annotate(n=Count("id")):
        cat_counts[row["category__parent__name"] or row["category__name"]] += row["n"]
    loc_counts = lost.values("location__full_path").annotate(n=Count("id")).order_by("-n")
    hour_counts = Counter(timezone.localtime(dt).hour for dt in lost.values_list("occurred_at", flat=True))
    peak_hour = max(hour_counts.items(), key=lambda kv: kv[1])[0] if hour_counts else None
    method_labels = dict(DisposalRecord.Method.choices)

    return {
        "lost_total": lost.count(),
        "found_total": found_total,
        "returned": returned,
        "return_rate": round(returned * 100 / found_total, 1) if found_total else None,
        "median_hours_to_return": round(statistics.median(durations), 1) if durations else None,
        "in_custody": Item.objects.found().filter(status__in=[Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW]).count(),
        "disposed": DisposalRecord.objects.count(),
        "disposal_by_method": _bars((method_labels.get(r["method"], r["method"]), r["n"]) for r in
                                    DisposalRecord.objects.values("method").annotate(n=Count("id")).order_by("-n")),
        "verification_attempts": attempts,
        "verification_failure_rate": round(failed * 100 / attempts, 1) if attempts else None,
        "outstanding_claims": Claim.objects.filter(status__in=[Claim.Status.PENDING_VERIFICATION, Claim.Status.UNDER_REVIEW]).count(),
        "outstanding_handovers": Claim.objects.filter(status=Claim.Status.APPROVED).count(),
        "category_bars": _bars(cat_counts.most_common()),
        "location_bars": _bars((r["location__full_path"], r["n"]) for r in loc_counts),
        "hour_bars": _bars([(f"{h:02d}", hour_counts.get(h, 0)) for h in range(24)], limit=24),
        "peak_hour": f"{peak_hour:02d}:00" if peak_hour is not None else None,
        "match_suggestions": MatchSuggestion.objects.count(),
    }


def security_metrics():
    now = timezone.now()
    queue = disposal.ageing_queue()
    return {
        "in_custody": Item.objects.found().filter(status__in=[Item.Status.IN_CUSTODY, Item.Status.AWAITING_HANDOVER,
                                                              Item.Status.DISPOSAL_REVIEW]).count(),
        "awaiting_intake": Item.objects.found().filter(status=Item.Status.OPEN).count(),
        "pending_review": Claim.objects.filter(status=Claim.Status.UNDER_REVIEW).count(),
        "approved_waiting": Claim.objects.filter(status=Claim.Status.APPROVED).count(),
        "upcoming_handovers": Handover.objects.filter(status=Handover.Status.SCHEDULED,
                                                      scheduled_for__gte=now - timezone.timedelta(hours=12))
        .select_related("claim__item", "claim__claimant", "location", "officer").order_by("scheduled_for")[:8],
        "ageing": queue[:6],
        "ageing_count": len(queue),
    }


def member_metrics(user):
    reports = Item.objects.owned_by(user)
    return {
        "active_reports": reports.filter(status__in=Item.PUBLIC_STATUSES).count(),
        "pending_claims": Claim.objects.filter(claimant=user, status__in=Claim.ACTIVE_STATUSES).count(),
        "matches": MatchSuggestion.objects.filter(lost_item__reporter=user, lost_item__status=Item.Status.OPEN,
                                                  status=MatchSuggestion.Status.SUGGESTED).count(),
        "unread": Notification.objects.filter(user=user, is_read=False).count(),
        "recent_reports": reports.select_related("category__parent", "location")[:5],
        "recent_claims": Claim.objects.filter(claimant=user).select_related("item")[:5],
        "notifications": Notification.objects.filter(user=user)[:5],
    }
