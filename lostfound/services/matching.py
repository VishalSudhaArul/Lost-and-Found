"""M3 — transparent, configurable matching between lost and found reports.

score = Σ weight_f · factor_f / Σ weight_f, with every factor in [0, 1]:

* category   — 1.0 same (sub)category, 0.6 same parent category, else 0
* text       — trigram similarity of the public search text. On PostgreSQL this
               is computed by pg_trgm ``similarity()``; elsewhere a pure-Python
               implementation of the same trigram algorithm is used.
* attributes — share of attributes present on both reports with equal values
* location   — 1.0 same place, 0.7 parent/child/sibling in the campus tree,
               0.4 same wider area, or distance decay when coordinates exist
               (0 beyond 1 km) — whichever is higher
* time       — linear decay over the configured window; a found time more than
               a day *before* the loss time scores 0

Only public fields are used. PrivateDetail is never read here.
"""
import math
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import connection, transaction

from ..models import Item, MatchSuggestion, OperationalSettings

TOKEN_RE = re.compile(r"[a-z0-9]+")


def trigrams(text):
    """pg_trgm-compatible trigram set: each word padded with two leading and
    one trailing space; non-alphanumerics are separators."""
    result = set()
    for word in TOKEN_RE.findall((text or "").lower()):
        padded = f"  {word} "
        for i in range(len(padded) - 2):
            result.add(padded[i:i + 3])
    return result


def trigram_similarity(a, b):
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def uses_pg_trgm():
    return connection.vendor == "postgresql"


@dataclass
class MatchResult:
    score: float
    components: dict = field(default_factory=dict)


def category_score(a, b):
    if a.category_id == b.category_id:
        return 1.0
    if a.category.root.pk == b.category.root.pk:
        return 0.6
    return 0.0


def _norm(value):
    return " ".join(TOKEN_RE.findall(str(value).lower()))


def attribute_score(a, b):
    a_attrs, b_attrs = a.attributes or {}, b.attributes or {}
    shared = [k for k in a_attrs if a_attrs.get(k) and b_attrs.get(k)]
    if not shared:
        return 0.0, []
    matched = [k for k in shared if _norm(a_attrs[k]) == _norm(b_attrs[k])]
    return len(matched) / len(shared), matched


def _haversine_m(p, q):
    lat1, lon1, lat2, lon2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


def location_score(a, b):
    la, lb = a.location, b.location
    if la.pk == lb.pk:
        return 1.0
    anc_a, anc_b = la.ancestor_ids(), lb.ancestor_ids()
    best = 0.0
    if la.pk in anc_b or lb.pk in anc_a or (la.parent_id and la.parent_id == lb.parent_id):
        best = 0.7
    elif set(anc_a) & set(anc_b):
        best = 0.4
    ca, cb = la.coordinates, lb.coordinates
    if ca and cb:
        best = max(best, max(0.0, 1 - _haversine_m(ca, cb) / 1000))
    return round(best, 4)


def time_score(lost, found, window_days):
    delta_days = (found.occurred_at - lost.occurred_at).total_seconds() / 86400
    if delta_days < -1:  # "found" well before it was lost: implausible
        return 0.0
    return max(0.0, 1 - abs(delta_days) / window_days)


def score_pair(lost, found, cfg=None, text_score=None):
    cfg = cfg or OperationalSettings.load()
    weights = cfg.weights
    if text_score is None:
        text_score = trigram_similarity(lost.search_text, found.search_text)
    attr, matched_attrs = attribute_score(lost, found)
    factors = {
        "category": category_score(lost, found),
        "text": round(float(text_score), 4),
        "attributes": round(attr, 4),
        "location": location_score(lost, found),
        "time": round(time_score(lost, found, cfg.match_time_window_days), 4),
    }
    total_weight = sum(weights.values()) or 1.0
    score = sum(weights[k] * factors[k] for k in factors) / total_weight
    return MatchResult(score=round(min(1.0, max(0.0, score)), 4),
                       components={"factors": factors, "weights": weights, "matched_attributes": matched_attrs,
                                   "engine": "pg_trgm" if uses_pg_trgm() else "python-trigram"})


def _candidates(item, cfg):
    window = timedelta(days=cfg.match_time_window_days)
    if item.is_lost:
        other_type, statuses = Item.ReportType.FOUND, [Item.Status.OPEN, Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW]
    else:
        other_type, statuses = Item.ReportType.LOST, [Item.Status.OPEN]
    qs = (Item.objects.filter(report_type=other_type, status__in=statuses,
                              occurred_at__gte=item.occurred_at - window, occurred_at__lte=item.occurred_at + window)
          .exclude(reporter_id=item.reporter_id)
          .select_related("category__parent", "location__parent__parent"))
    if uses_pg_trgm():
        from django.contrib.postgres.search import TrigramSimilarity
        qs = qs.annotate(text_similarity=TrigramSimilarity("search_text", item.search_text))
    return qs[:500]


@transaction.atomic
def refresh_for_item(item, cfg=None, notify_owner=True):
    """Recompute suggestions involving ``item``; returns those at/above threshold."""
    from . import notifications

    cfg = cfg or OperationalSettings.load()
    side = {"lost_item": item} if item.is_lost else {"found_item": item}
    if item.status in Item.TERMINAL_STATUSES or item.status == Item.Status.AWAITING_HANDOVER:
        MatchSuggestion.objects.filter(status=MatchSuggestion.Status.SUGGESTED, **side).delete()
        return []
    kept = []
    for other in _candidates(item, cfg):
        lost, found = (item, other) if item.is_lost else (other, item)
        result = score_pair(lost, found, cfg, text_score=getattr(other, "text_similarity", None))
        existing = MatchSuggestion.objects.filter(lost_item=lost, found_item=found).first()
        if result.score < cfg.match_threshold:
            if existing and existing.status == MatchSuggestion.Status.SUGGESTED:
                existing.delete()
            continue
        if existing:
            if existing.status != MatchSuggestion.Status.SUGGESTED:
                continue
            existing.score, existing.components = result.score, result.components
            existing.save(update_fields=["score", "components", "updated_at"])
            suggestion = existing
        else:
            suggestion = MatchSuggestion.objects.create(
                lost_item=lost, found_item=found, score=result.score, components=result.components)
        kept.append(suggestion)
        if notify_owner and not suggestion.owner_notified:
            notifications.notify(
                [lost.reporter], "match", "Possible match for your lost item",
                f"A found item may match your report {lost.reference} ({lost.title}).",
                link=f"/my/reports/{lost.pk}/matches/", dedupe_key=f"match:{lost.pk}:{found.pk}")
            suggestion.owner_notified = True
            suggestion.save(update_fields=["owner_notified"])
    return kept


def refresh_all(cfg=None):
    cfg = cfg or OperationalSettings.load()
    count = 0
    lost_items = Item.objects.lost().filter(status=Item.Status.OPEN).select_related("category__parent", "location__parent__parent")
    for item in lost_items.iterator(chunk_size=200):
        count += len(refresh_for_item(item, cfg))
    # Drop stale suggestions whose found item is no longer available.
    MatchSuggestion.objects.filter(status=MatchSuggestion.Status.SUGGESTED).exclude(
        found_item__status__in=[Item.Status.OPEN, Item.Status.IN_CUSTODY, Item.Status.DISPOSAL_REVIEW]).delete()
    return count


FACTOR_LABELS = {"category": "Category", "text": "Description similarity", "attributes": "Matching attributes",
                 "location": "Location", "time": "Time proximity"}


def explain(suggestion):
    """Human-readable factor list (no private data)."""
    factors = suggestion.components.get("factors", {})
    weights = suggestion.components.get("weights", {})
    return [{"label": label, "value": round(factors.get(key, 0) * 100), "weight": weights.get(key, 0)}
            for key, label in FACTOR_LABELS.items()]
