from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditEntry(models.Model):
    """Append-only audit log. Never store passwords, OTPs, keys or answers."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    summary = models.CharField(max_length=300)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "audit entries"
        indexes = [models.Index(fields=["target_type", "target_id"], name="audit_target")]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Audit entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit entries cannot be deleted.")


class Notification(models.Model):
    class Kind(models.TextChoices):
        MATCH = "match", "Possible match"
        CLAIM = "claim", "Claim update"
        VERIFICATION = "verification", "Verification"
        HANDOVER = "handover", "Handover"
        AGEING = "ageing", "Ageing / retention"
        DISPOSAL = "disposal", "Disposal"
        SYSTEM = "system", "System"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=14, choices=Kind.choices)
    title = models.CharField(max_length=140)
    message = models.CharField(max_length=500)
    link = models.CharField(max_length=300, blank=True)
    dedupe_key = models.CharField(max_length=120, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "dedupe_key"], condition=~models.Q(dedupe_key=""),
                                    name="uniq_notification_dedupe"),
        ]
        indexes = [models.Index(fields=["user", "is_read"], name="notif_user_unread")]

    def __str__(self):
        return f"{self.user}: {self.title}"
