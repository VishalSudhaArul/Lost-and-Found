from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify


class ItemCategory(models.Model):
    """Two-level category tree (category -> subcategory)."""

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    description = models.CharField(max_length=200, blank=True)
    retention_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Overrides the default retention period for this category.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["parent__name", "name"]
        verbose_name_plural = "item categories"
        constraints = [
            models.UniqueConstraint(fields=["parent", "name"], name="uniq_category_name_per_parent"),
        ]

    def __str__(self):
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name

    def clean(self):
        if self.parent_id and self.parent.parent_id:
            raise ValidationError("Categories support two levels: category and subcategory.")
        if self.parent_id and self.pk and self.parent_id == self.pk:
            raise ValidationError("A category cannot be its own parent.")

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(str(self) if self.parent_id else self.name)[:90] or "category"
            slug, n = base, 2
            while ItemCategory.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def root(self):
        return self.parent if self.parent_id else self

    def all_attributes(self):
        """Attributes defined on this category and its parent."""
        ids = [self.pk] + ([self.parent_id] if self.parent_id else [])
        return CategoryAttribute.objects.filter(category_id__in=ids, is_active=True).order_by("sort_order", "label")

    def effective_retention_days(self, default):
        if self.retention_days:
            return self.retention_days
        if self.parent_id and self.parent.retention_days:
            return self.parent.retention_days
        return default


class CategoryAttribute(models.Model):
    """A structured, PUBLIC attribute (e.g. colour, brand). Secret identifying
    details never go here — they belong in PrivateDetail."""

    class Kind(models.TextChoices):
        TEXT = "text", "Free text"
        CHOICE = "choice", "Choice"

    category = models.ForeignKey(ItemCategory, on_delete=models.CASCADE, related_name="attributes")
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=60)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.TEXT)
    choices = models.CharField(max_length=500, blank=True, help_text="Comma-separated options for 'Choice' attributes.")
    used_for_matching = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "label"]
        constraints = [models.UniqueConstraint(fields=["category", "key"], name="uniq_attribute_key_per_category")]

    def __str__(self):
        return f"{self.category}: {self.label}"

    @property
    def choice_list(self):
        return [c.strip() for c in self.choices.split(",") if c.strip()]


class Location(models.Model):
    """Campus location tree: campus area > building/block > floor > room/area."""

    class Kind(models.TextChoices):
        AREA = "area", "Campus area"
        BUILDING = "building", "Building / block"
        FLOOR = "floor", "Floor"
        ROOM = "room", "Room / spot"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.BUILDING)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    code = models.CharField(max_length=30, blank=True, help_text="Short code, e.g. 'B34' or 'LIB-2'.")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                    validators=[MinValueValidator(-180), MaxValueValidator(180)])
    is_custody_point = models.BooleanField(default=False, help_text="Security office / locker where items are held.")
    is_active = models.BooleanField(default=True)
    full_path = models.CharField(max_length=400, editable=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_path"]
        constraints = [models.UniqueConstraint(fields=["parent", "name"], name="uniq_location_name_per_parent")]
        indexes = [models.Index(fields=["is_active", "is_custody_point"], name="location_active_custody")]

    def __str__(self):
        return self.full_path or self.name

    def clean(self):
        node, seen = self.parent, set()
        while node is not None:
            if (self.pk and node.pk == self.pk) or node.pk in seen:
                raise ValidationError("A location cannot be nested inside itself.")
            seen.add(node.pk)
            node = node.parent
        if (self.latitude is None) != (self.longitude is None):
            raise ValidationError("Provide both latitude and longitude, or neither.")

    def compute_path(self):
        parts, node, guard = [], self, 0
        while node is not None and guard < 10:
            parts.append(node.name)
            node, guard = node.parent, guard + 1
        return " › ".join(reversed(parts))

    def save(self, *args, **kwargs):
        self.full_path = self.compute_path()
        super().save(*args, **kwargs)
        for child in self.children.all():
            child.save()

    def ancestor_ids(self):
        ids, node = [], self.parent
        while node is not None and len(ids) < 10:
            ids.append(node.pk)
            node = node.parent
        return ids

    @property
    def coordinates(self):
        """Own coordinates, or the nearest ancestor's."""
        node = self
        while node is not None:
            if node.latitude is not None and node.longitude is not None:
                return float(node.latitude), float(node.longitude)
            node = node.parent
        return None
