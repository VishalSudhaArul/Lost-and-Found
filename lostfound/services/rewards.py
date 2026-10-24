"""Community points, tiers, badges and the leaderboard.

Rules enforced here (not in views or templates):
* every award has a unique ledger ``reference`` (e.g. ``found_report:12``), so the
  same event can never pay twice, even under concurrent requests;
* awards happen only after a confirmed event: a team-verified report, a custody
  receipt, or a completed supervised handover;
* no points for rejected reports or for returning an item to yourself;
* users can't change their balance: corrections are new, staff-authorised entries;
* points never influence claims, verification or handover.
"""
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from ..models import (AchievementBadge, Item, PointsLedgerEntry, RewardPolicy, UserBadge, UserRewardProfile)
from . import audit, notifications
from .errors import WorkflowError

Cat = PointsLedgerEntry.Category


def policy():
    return RewardPolicy.load()


def _award(user, category, points, reference, reason, item=None, handover=None, awarded_by=None):
    """Create one ledger entry, or return None if that event was already recorded."""
    if points <= 0 or user is None or not user.is_active:
        return None
    try:
        with transaction.atomic():
            entry = PointsLedgerEntry.objects.create(
                user=user, category=category, points=points, reference=reference, reason=reason[:200],
                item=item, handover=handover, awarded_by=awarded_by)
    except IntegrityError:
        return None  # already awarded for this event
    evaluate_badges(user)
    notifications.notify([user], "system", f"+{points} community points",
                         f"{reason}. Thank you for helping someone on campus.", link="/rewards/")
    return entry


# --- Event hooks (called from the review, custody and handover services) -------

def on_found_report_verified(item, reviewer):
    cfg = policy()
    if not cfg.rewards_enabled or not item.is_found or item.review_status != Item.Review.VERIFIED:
        return None
    return _award(item.reporter, Cat.REPORT, cfg.points_found_report, f"found_report:{item.pk}",
                  f"Found report {item.reference} verified", item=item, awarded_by=reviewer)


def on_quality_bonus(item, reviewer):
    cfg = policy()
    if not cfg.rewards_enabled or item.review_status != Item.Review.VERIFIED or reviewer.pk == item.reporter_id:
        return None
    return _award(item.reporter, Cat.BONUS, cfg.points_quality_bonus, f"quality_bonus:{item.pk}",
                  f"Exceptionally complete report {item.reference}", item=item, awarded_by=reviewer)


def on_custody_received(item, officer):
    cfg = policy()
    if not cfg.rewards_enabled or item.review_status == Item.Review.REJECTED or officer.pk == item.reporter_id:
        return None
    return _award(item.reporter, Cat.CUSTODY, cfg.points_custody, f"custody:{item.pk}",
                  f"Handed {item.reference} in to the Lost & Found team", item=item, awarded_by=officer)


def on_handover_completed(handover):
    cfg = policy()
    claim = handover.claim
    item = claim.item
    if not cfg.rewards_enabled or item.review_status == Item.Review.REJECTED:
        return None
    if item.reporter_id == claim.claimant_id:  # no self-dealing
        return None
    return _award(item.reporter, Cat.RETURN, cfg.points_return, f"return:{item.pk}",
                  f"{item.reference} returned to its owner", item=item, handover=handover,
                  awarded_by=handover.completed_by)


# --- Staff actions -------------------------------------------------------------

def award_community(user, points, reason, actor, request=None):
    if not actor.has_perm("lostfound.manage_rewards"):
        raise PermissionDenied
    cfg = policy()
    if actor.pk == user.pk:
        raise WorkflowError("You can't award points to yourself.")
    if not 1 <= points <= cfg.max_community_award:
        raise WorkflowError(f"Community awards must be between 1 and {cfg.max_community_award} points.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise WorkflowError("Give a short reason for the award.")
    ref = f"community:{user.pk}:{timezone.now().strftime('%Y%m%d%H%M%S%f')}"
    entry = _award(user, Cat.COMMUNITY, points, ref, reason, awarded_by=actor)
    audit.record(actor, "rewards.community_awarded", f"{points} community points to {user.username}",
                 target=user, request=request, points=points)
    return entry


def reverse_entry(entry_id, actor, reason, request=None):
    if not actor.has_perm("lostfound.manage_rewards"):
        raise PermissionDenied
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise WorkflowError("Give a reason for the correction.")
    with transaction.atomic():
        original = PointsLedgerEntry.objects.select_for_update().get(pk=entry_id)
        if original.points <= 0 or original.category == Cat.CORRECTION:
            raise WorkflowError("Only awards can be reversed.")
        if original.is_reversed:
            raise WorkflowError("This entry has already been reversed.")
        try:
            correction = PointsLedgerEntry.objects.create(
                user=original.user, category=Cat.CORRECTION, points=-original.points, reverses=original,
                reference=f"reversal:{original.pk}", reason=f"Correction: {reason}"[:200],
                item=original.item, handover=original.handover, awarded_by=actor)
        except IntegrityError:
            raise WorkflowError("This entry has already been reversed.")
    revoked = evaluate_badges(original.user, revoke=True)
    audit.record(actor, "rewards.reversed", f"Points entry #{original.pk} reversed for {original.user.username}",
                 target=original.user, request=request, points=original.points, revoked_badges=revoked)
    return correction


# --- Read models -------------------------------------------------------------

def _valid_awards(user):
    """Positive entries that haven't been reversed."""
    return PointsLedgerEntry.objects.filter(user=user, points__gt=0, reversal__isnull=True)


def metrics(user):
    awards = _valid_awards(user)
    return {
        "verified_actions": awards.filter(category__in=PointsLedgerEntry.ACTION_CATEGORIES).count(),
        "found_reports": awards.filter(category=Cat.REPORT).count(),
        "returns": awards.filter(category=Cat.RETURN).count(),
    }


def evaluate_badges(user, revoke=False):
    """Grant any newly met badges once; with ``revoke`` drop those no longer met (after a correction)."""
    values = metrics(user)
    held = set(UserBadge.objects.filter(user=user).values_list("badge_id", flat=True))
    revoked = []
    for badge in AchievementBadge.objects.filter(is_active=True):
        met = values.get(badge.metric, 0) >= badge.threshold
        if met and badge.pk not in held:
            _, created = UserBadge.objects.get_or_create(user=user, badge=badge)
            if created:
                notifications.notify([user], "system", f"New badge: {badge.name}", badge.description, link="/rewards/")
        elif revoke and not met and badge.pk in held:
            UserBadge.objects.filter(user=user, badge=badge).delete()
            revoked.append(badge.code)
    return revoked


def profile_for(user):
    profile, _ = UserRewardProfile.objects.get_or_create(user=user)
    return profile


def summary(user):
    entries = PointsLedgerEntry.objects.filter(user=user)
    totals = entries.aggregate(balance=Sum("points"), lifetime=Sum("points", filter=Q(points__gt=0)))
    by_cat = {row["category"]: row["total"] for row in
              entries.values("category").annotate(total=Sum("points")).order_by()}
    m = metrics(user)
    tiers = list(AchievementBadge.objects.filter(kind=AchievementBadge.Kind.TIER, is_active=True).order_by("threshold"))
    earned_ids = set(UserBadge.objects.filter(user=user).values_list("badge_id", flat=True))
    current = next_tier = None
    for tier in tiers:
        if tier.pk in earned_ids or m["verified_actions"] >= tier.threshold:
            current = tier
        elif next_tier is None:
            next_tier = tier
    progress = None
    if next_tier:
        start = current.threshold if current else 0
        span = max(next_tier.threshold - start, 1)
        progress = {"tier": next_tier, "remaining": max(next_tier.threshold - m["verified_actions"], 0),
                    "pct": min(100, round((m["verified_actions"] - start) * 100 / span))}
    return {
        "balance": totals["balance"] or 0,
        "lifetime": totals["lifetime"] or 0,
        "by_category": [{"key": k, "label": Cat(k).label, "points": by_cat.get(k, 0)}
                        for k in (Cat.REPORT, Cat.CUSTODY, Cat.RETURN, Cat.BONUS, Cat.COMMUNITY)],
        "corrections": by_cat.get(Cat.CORRECTION, 0),
        "metrics": m,
        "tiers": [{"tier": t, "earned": t.pk in earned_ids} for t in tiers],
        "current_tier": current,
        "progress": progress,
        "badges": UserBadge.objects.filter(user=user, badge__kind=AchievementBadge.Kind.BADGE).select_related("badge"),
        "all_badges": AchievementBadge.objects.filter(kind=AchievementBadge.Kind.BADGE, is_active=True),
        "earned_ids": earned_ids,
        "ledger": entries.select_related("item", "reverses").prefetch_related("reversal")[:20],
    }


def leaderboard(limit=20):
    """Ranks opted-in members by points earned in the configured period.
    Shows only a public display name — never emails, usernames or report details."""
    cfg = policy()
    if not cfg.leaderboard_enabled:
        return []
    since = timezone.now() - timedelta(days=cfg.leaderboard_period_days)
    period = PointsLedgerEntry.objects.filter(created_at__gte=since, user__is_active=True).exclude(
        user__reward_profile__show_on_leaderboard=False)
    rows = list(period.values("user").annotate(points=Sum("points")).filter(points__gt=0)
                .order_by("-points", "user")[:limit])
    ids = [r["user"] for r in rows]
    returns = dict(period.filter(user__in=ids, category=Cat.RETURN, points__gt=0, reversal__isnull=True)
                   .values("user").annotate(n=Count("pk")).values_list("user", "n"))
    for r in rows:
        r["returns"] = returns.get(r["user"], 0)
    from django.contrib.auth import get_user_model
    users = {u.pk: u for u in get_user_model().objects.filter(pk__in=ids).select_related("reward_profile")}
    top_tier = {}
    for ub in (UserBadge.objects.filter(user__in=users, badge__kind=AchievementBadge.Kind.TIER)
               .select_related("badge").order_by("badge__threshold")):
        top_tier[ub.user_id] = ub.badge
    result = []
    for rank, row in enumerate(rows, start=1):
        user = users[row["user"]]
        profile = getattr(user, "reward_profile", None) or UserRewardProfile(user=user)
        result.append({"rank": rank, "name": profile.public_name(), "points": row["points"],
                       "returns": row["returns"], "tier": top_tier.get(user.pk), "user_id": user.pk})
    return result


def community_stats():
    """Homepage figures, all from database rows."""
    contributors = (PointsLedgerEntry.objects.filter(points__gt=0, reversal__isnull=True)
                    .values("user").distinct().count())
    return {"contributors": contributors}
