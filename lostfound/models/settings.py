from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class OperationalSettings(models.Model):
    """Singleton row holding configurable policy. Retention fields are edited by
    campus administrators; matching/verification fields by system administrators."""

    # Retention / ageing (campus administrator)
    default_retention_days = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    expiry_warning_days = models.PositiveIntegerField(
        default=7, help_text="Warn this many days before the retention period ends.")
    escalation_days = models.PositiveIntegerField(
        default=14, help_text="Escalate to campus administrators if still undisposed this many days after expiry.")

    # Matching (system administrator)
    match_threshold = models.FloatField(default=0.45, validators=[MinValueValidator(0), MaxValueValidator(1)])
    match_time_window_days = models.PositiveIntegerField(default=30, validators=[MinValueValidator(1)])
    weight_category = models.FloatField(default=0.25, validators=[MinValueValidator(0)])
    weight_text = models.FloatField(default=0.35, validators=[MinValueValidator(0)])
    weight_attributes = models.FloatField(default=0.15, validators=[MinValueValidator(0)])
    weight_location = models.FloatField(default=0.15, validators=[MinValueValidator(0)])
    weight_time = models.FloatField(default=0.10, validators=[MinValueValidator(0)])

    # Verification & handover (system administrator)
    challenge_question_count = models.PositiveIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(10)])
    verification_max_attempts = models.PositiveIntegerField(default=3, validators=[MinValueValidator(1), MaxValueValidator(10)])
    verification_pass_threshold = models.FloatField(
        default=0.67, validators=[MinValueValidator(0.1), MaxValueValidator(1)],
        help_text="Fraction of the challenge (by weight) that must be answered correctly.")
    otp_ttl_minutes = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(60)])
    otp_max_attempts = models.PositiveIntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(10)])

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "operational settings"
        verbose_name_plural = "operational settings"

    def __str__(self):
        return "Operational settings"

    def clean(self):
        total = self.weight_category + self.weight_text + self.weight_attributes + self.weight_location + self.weight_time
        if total <= 0:
            raise ValidationError("At least one matching weight must be greater than zero.")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover - singleton is never deleted
        raise ValidationError("Operational settings cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def weights(self):
        return {
            "category": self.weight_category,
            "text": self.weight_text,
            "attributes": self.weight_attributes,
            "location": self.weight_location,
            "time": self.weight_time,
        }
