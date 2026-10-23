import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .taxonomy import ItemCategory, Location

# The ONLY item fields that may ever leave the server through public views,
# public JSON or public search. PrivateDetail is a separate model with no
# reverse accessor on Item, so it cannot be reached from these rows.
PUBLIC_ITEM_FIELDS = (
    "id", "public_id", "report_type", "title", "description", "category_id",
    "location_id", "occurred_at", "status", "photo", "attributes", "created_at",
)


class ItemQuerySet(models.QuerySet):
    def lost(self):
        return self.filter(report_type=Item.ReportType.LOST)

    def found(self):
        return self.filter(report_type=Item.ReportType.FOUND)

    def publicly_listed(self):
        return self.filter(status__in=Item.PUBLIC_STATUSES, is_hidden=False)

    def owned_by(self, user):
        return self.filter(reporter=user)


class Item(models.Model):
    class ReportType(models.TextChoices):
        LOST = "lost", "Lost"
        FOUND = "found", "Found"

    class Status(models.TextChoices):
        OPEN = "open", "Open"                          # lost: searching / found: awaiting drop-off
        IN_CUSTODY = "in_custody", "In custody"        # found item held by security
        AWAITING_HANDOVER = "awaiting_handover", "Awaiting handover"
        RETURNED = "returned", "Returned"
        DISPOSAL_REVIEW = "disposal_review", "Disposal review"
        DISPOSED = "disposed", "Disposed"
        CLOSED = "closed", "Closed"                    # withdrawn by reporter / resolved elsewhere

    PUBLIC_STATUSES = (Status.OPEN, Status.IN_CUSTODY, Status.AWAITING_HANDOVER)
    TERMINAL_STATUSES = (Status.RETURNED, Status.DISPOSED, Status.CLOSED)

    TRANSITIONS = {
        ReportType.LOST: {
            Status.OPEN: {Status.RETURNED, Status.CLOSED},
            Status.CLOSED: {Status.OPEN},
        },
        ReportType.FOUND: {
            Status.OPEN: {Status.IN_CUSTODY, Status.CLOSED},
            Status.IN_CUSTODY: {Status.AWAITING_HANDOVER, Status.DISPOSAL_REVIEW},
            Status.AWAITING_HANDOVER: {Status.RETURNED, Status.IN_CUSTODY},
            Status.DISPOSAL_REVIEW: {Status.DISPOSED, Status.IN_CUSTODY, Status.AWAITING_HANDOVER},
        },
    }

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    report_type = models.CharField(max_length=5, choices=ReportType.choices)
    title = models.CharField(max_length=120)
    description = models.TextField(max_length=1500, help_text="General description shown publicly.")
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, related_name="items")
    attributes = models.JSONField(default=dict, blank=True, help_text="Public category attributes (colour, brand…).")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="items")
    location_note = models.CharField(max_length=200, blank=True)
    occurred_at = models.DateTimeField(help_text="When the item was lost or found.")
    photo = models.ImageField(upload_to="items/%Y/%m/", blank=True)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reports")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    is_hidden = models.BooleanField(default=False, help_text="Hidden from the public board by moderators.")
    search_text = models.TextField(blank=True, editable=False)
    custody_since = models.DateTimeField(null=True, blank=True, editable=False)
    current_custody_location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.PROTECT, related_name="items_in_custody", editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    retention_extension_days = models.PositiveIntegerField(default=0, editable=False)
    disposal_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+", editable=False)
    disposal_reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = ItemQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["report_type", "status", "-created_at"], name="item_type_status_created"),
            models.Index(fields=["category", "report_type"], name="item_category_type"),
            models.Index(fields=["location", "occurred_at"], name="item_location_time"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(report_type="found") | models.Q(status__in=["open", "returned", "closed"]),
                name="lost_item_status_valid",
            ),
        ]

    def __str__(self):
        return f"{self.reference} {self.title}"

    @property
    def reference(self):
        prefix = "F" if self.report_type == self.ReportType.FOUND else "L"
        return f"{prefix}-{self.pk or 0:05d}"

    @property
    def is_found(self):
        return self.report_type == self.ReportType.FOUND

    @property
    def is_lost(self):
        return self.report_type == self.ReportType.LOST

    def clean(self):
        if self.occurred_at and self.occurred_at > timezone.now() + timezone.timedelta(minutes=5):
            raise ValidationError({"occurred_at": "The date cannot be in the future."})

    def build_search_text(self):
        attrs = " ".join(str(v) for v in (self.attributes or {}).values() if v)
        cat = str(self.category) if self.category_id else ""
        return " ".join(p for p in [self.title, self.description, cat, attrs] if p).lower()

    def save(self, *args, **kwargs):
        self.search_text = self.build_search_text()
        if "update_fields" in kwargs and kwargs["update_fields"] is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"search_text"}
        super().save(*args, **kwargs)

    def can_transition(self, new_status):
        return new_status in self.TRANSITIONS.get(self.report_type, {}).get(self.status, set())

    def transition_to(self, new_status, save=True):
        if not self.can_transition(new_status):
            raise ValidationError(
                f"{self.get_report_type_display()} item cannot move from "
                f"'{self.get_status_display()}' to '{self.Status(new_status).label}'.")
        self.status = new_status
        if new_status in self.TERMINAL_STATUSES:
            self.resolved_at = timezone.now()
        if save:
            self.save(update_fields=["status", "resolved_at", "updated_at"])

    @property
    def is_public(self):
        return self.status in self.PUBLIC_STATUSES and not self.is_hidden

    def public_attributes(self):
        """[(label, value)] for attributes defined on the category."""
        if not self.attributes:
            return []
        labels = {a.key: a.label for a in self.category.all_attributes()}
        return [(labels[k], v) for k, v in self.attributes.items() if k in labels and v]
