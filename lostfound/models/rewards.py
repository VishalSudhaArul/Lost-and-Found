"""Community recognition: configurable policy, an append-only points ledger,
achievement tiers/badges and per-user leaderboard preferences.

Points are recognition inside the platform only. They never affect claim
priority or ownership verification, and they are not cash."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class RewardPolicy(models.Model):
    """Singleton row with the points policy (edited by campus administrators)."""

    rewards_enabled = models.BooleanField(default=True)
    points_found_report = models.PositiveIntegerField(
        default=10, validators=[MaxValueValidator(500)], help_text="Found report verified by the Lost & Found team.")
    points_custody = models.PositiveIntegerField(
        default=5, validators=[MaxValueValidator(500)], help_text="Item handed in and received into custody.")
    points_return = models.PositiveIntegerField(
        default=25, validators=[MaxValueValidator(500)], help_text="Item returned to its verified owner.")
    points_quality_bonus = models.PositiveIntegerField(
        default=5, validators=[MaxValueValidator(500)], help_text="Bonus a reviewer may give for an exceptionally complete report.")
    max_community_award = models.PositiveIntegerField(
        default=50, validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text="Largest single community-contribution award an administrator can record.")
    leaderboard_enabled = models.BooleanField(default=True)
    leaderboard_period_days = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(7), MaxValueValidator(365)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = verbose_name_plural = "reward policy"

    def __str__(self):
        return "Reward policy"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PointsLedgerEntry(models.Model):
    """Append-only. Corrections are new negative entries that reference the original."""

    class Category(models.TextChoices):
        REPORT = "report", "Found report"
        CUSTODY = "custody", "Handed in to security"
        RETURN = "return", "Successful return"
        BONUS = "bonus", "Reviewer bonus"
        COMMUNITY = "community", "Community contribution"
        CORRECTION = "correction", "Correction"

    ACTION_CATEGORIES = (Category.REPORT, Category.CUSTODY, Category.RETURN, Category.COMMUNITY)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="points_entries")
    category = models.CharField(max_length=12, choices=Category.choices, db_index=True)
    points = models.IntegerField()
    reason = models.CharField(max_length=200)
    reference = models.CharField(max_length=80, unique=True,
                                 help_text="Unique event key, e.g. found_report:12. Prevents duplicate awards.")
    item = models.ForeignKey("lostfound.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    handover = models.ForeignKey("lostfound.Handover", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    awarded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", help_text="Staff member who authorised it; empty for system events.")
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["user", "created_at"])]
        verbose_name_plural = "points ledger entries"

    def __str__(self):
        return f"{self.user} {self.points:+d} ({self.category})"

    def save(self, *args, **kwargs):
        if self.pk and PointsLedgerEntry.objects.filter(pk=self.pk).exists():
            raise ValidationError("Ledger entries cannot be edited; record a correction instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted; record a correction instead.")

    @property
    def is_reversed(self):
        return hasattr(self, "reversal")


class AchievementBadge(models.Model):
    class Kind(models.TextChoices):
        TIER = "tier", "Achievement tier"
        BADGE = "badge", "Badge"

    class Metric(models.TextChoices):
        VERIFIED_ACTIONS = "verified_actions", "Verified helpful actions"
        FOUND_REPORTS = "found_reports", "Verified found reports"
        RETURNS = "returns", "Successful returns"

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200)
    kind = models.CharField(max_length=6, choices=Kind.choices, default=Kind.BADGE)
    metric = models.CharField(max_length=20, choices=Metric.choices)
    threshold = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    style = models.CharField(max_length=12, default="bronze",
                             choices=[(s, s.title()) for s in ("bronze", "silver", "gold", "platinum", "indigo", "green")])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "threshold", "name"]

    def __str__(self):
        return self.name


class UserBadge(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="badges")
    badge = models.ForeignKey(AchievementBadge, on_delete=models.PROTECT, related_name="holders")
    awarded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "badge"], name="unique_user_badge")]
        ordering = ["-awarded_at"]


class UserRewardProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reward_profile")
    display_name = models.CharField(max_length=40, blank=True,
                                    help_text="Nickname shown on the leaderboard. Leave blank to use your first name and initial.")
    show_on_leaderboard = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def public_name(self):
        if self.display_name:
            return self.display_name
        u = self.user
        if u.first_name:
            return f"{u.first_name} {u.last_name[:1]}.".strip() if u.last_name else u.first_name
        return "Campus member"
