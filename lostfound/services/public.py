"""M8 — the public, redacted board.

Public data is selected with ``.values()`` over an explicit allow-list and
converted to frozen ``PublicItem`` objects. Public templates and the JSON
endpoint only ever receive ``PublicItem``; they never receive model
instances, so there is no object graph from which private data — or the
reporter's identity — could leak."""
from dataclasses import asdict, dataclass

from django.core.files.storage import default_storage
from django.db import connection
from django.db.models import Q

from ..models import Item, ItemCategory, Location

PUBLIC_VALUES = (
    "id", "report_type", "title", "description", "occurred_at", "status", "photo", "attributes",
    "created_at", "category__name", "category__parent__name", "location__full_path",
)


@dataclass(frozen=True)
class PublicItem:
    id: int
    reference: str
    report_type: str
    title: str
    description: str
    category: str
    location: str
    occurred_at: object
    status: str
    status_label: str
    photo_url: str
    attributes: dict
    created_at: object

    @property
    def is_found(self):
        return self.report_type == Item.ReportType.FOUND

    def as_json(self):
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        data["created_at"] = self.created_at.isoformat()
        return data


def _to_public(row):
    parent = row["category__parent__name"]
    category = f"{parent} › {row['category__name']}" if parent else row["category__name"]
    prefix = "F" if row["report_type"] == Item.ReportType.FOUND else "L"
    return PublicItem(
        id=row["id"], reference=f"{prefix}-{row['id']:05d}", report_type=row["report_type"], title=row["title"],
        description=row["description"], category=category, location=row["location__full_path"],
        occurred_at=row["occurred_at"], status=row["status"], status_label=Item.Status(row["status"]).label,
        photo_url=default_storage.url(row["photo"]) if row["photo"] else "",
        attributes={str(k): str(v) for k, v in (row["attributes"] or {}).items() if v}, created_at=row["created_at"],
    )


def search_backend():
    return "PostgreSQL full-text + pg_trgm" if connection.vendor == "postgresql" else "basic substring (SQLite fallback)"


def board_queryset(params):
    qs = Item.objects.publicly_listed()
    report_type = params.get("type")
    if report_type in Item.ReportType.values:
        qs = qs.filter(report_type=report_type)
    category = params.get("category")
    if category and str(category).isdigit():
        qs = qs.filter(Q(category_id=category) | Q(category__parent_id=category))
    location = params.get("location")
    if location and str(location).isdigit():
        loc = Location.objects.filter(pk=location).first()
        if loc:
            qs = qs.filter(Q(location=loc) | Q(location__full_path__startswith=loc.full_path + " › "))
    for key, lookup in (("from", "occurred_at__date__gte"), ("to", "occurred_at__date__lte")):
        value = params.get(key)
        if value:
            try:
                from datetime import date
                qs = qs.filter(**{lookup: date.fromisoformat(value)})
            except ValueError:
                pass

    q = (params.get("q") or "").strip()[:100]
    sort = params.get("sort", "newest")
    if q:
        if connection.vendor == "postgresql":
            from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramWordSimilarity
            vector = SearchVector("title", weight="A", config="english") + SearchVector("search_text", weight="B", config="english")
            query = SearchQuery(q, search_type="websearch", config="english")
            qs = (qs.annotate(rank=SearchRank(vector, query), fuzzy=TrigramWordSimilarity(q, "search_text"))
                  .filter(Q(rank__gt=0.001) | Q(fuzzy__gte=0.45)))
            if sort == "relevance":
                return qs.order_by("-rank", "-fuzzy", "-created_at")
        else:
            for word in q.lower().split()[:6]:
                qs = qs.filter(search_text__icontains=word)
    if sort == "oldest":
        return qs.order_by("created_at")
    if sort == "occurred":
        return qs.order_by("-occurred_at")
    return qs.order_by("-created_at")


def to_public_items(qs):
    return [_to_public(row) for row in qs.values(*PUBLIC_VALUES)]


def public_item(pk):
    row = Item.objects.publicly_listed().filter(pk=pk).values(*PUBLIC_VALUES).first()
    return _to_public(row) if row else None


def filter_options():
    return {
        "categories": ItemCategory.objects.filter(is_active=True, parent__isnull=True).prefetch_related("children"),
        "locations": Location.objects.filter(is_active=True).exclude(kind=Location.Kind.ROOM),
    }


def summary(pk):
    """Redacted DTO for an item regardless of board visibility (used on a
    claimant's own claim pages after the item leaves the public board)."""
    row = Item.objects.filter(pk=pk).values(*PUBLIC_VALUES).first()
    return _to_public(row) if row else None
