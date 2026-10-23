import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from ..services.storage import private_storage_factory


class AppendOnlyModel(models.Model):
    """Rows can be inserted but never updated or deleted through the ORM.
    On PostgreSQL a trigger enforces the same rule at database level.
    Mistakes are fixed by inserting a correction row that references the
    original (see docs/CUSTODY.md)."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(f"{self.__class__.__name__} records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"{self.__class__.__name__} records cannot be deleted.")


class CustodyRecord(AppendOnlyModel):
    class Action(models.TextChoices):
        RECEIVED = "received", "Received into custody"
        TRANSFERRED = "transferred", "Transferred"
        RELEASED = "released", "Released to owner"
        DISPOSED = "disposed", "Disposed"
        CORRECTION = "correction", "Correction"

    item = models.ForeignKey("lostfound.Item", on_delete=models.PROTECT, related_name="custody_records")
    action = models.CharField(max_length=24, choices=Action.choices)
    from_location = models.ForeignKey("lostfound.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_location = models.ForeignKey("lostfound.Location", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    received_from = models.CharField(max_length=150, blank=True, help_text="Person or unit handing the item over.")
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    notes = models.CharField(max_length=500, blank=True)
    corrects = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="corrections")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.item.reference} {self.get_action_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


def _receipt():
    return "H-" + secrets.token_hex(5).upper()


class Handover(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Method(models.TextChoices):
        OTP = "otp", "One-time code shown on claimant's account"
        SIGNATURE = "signature", "Claimant signature"

    class IdType(models.TextChoices):
        STUDENT_ID = "student_id", "University student ID card"
        STAFF_ID = "staff_id", "University staff ID card"
        GOVT_ID = "govt_id", "Government photo ID"

    claim = models.OneToOneField("lostfound.Claim", on_delete=models.PROTECT, related_name="handover")
    location = models.ForeignKey("lostfound.Location", on_delete=models.PROTECT, related_name="+")
    scheduled_for = models.DateTimeField()
    officer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_handovers")
    scheduled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.OTP)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED, db_index=True)

    # OTP: only a salted hash is stored; plaintext is shown once to the claimant.
    otp_hash = models.CharField(max_length=128, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_used_at = models.DateTimeField(null=True, blank=True)

    # Completion evidence (no ID numbers or document images are stored).
    id_type_checked = models.CharField(max_length=12, choices=IdType.choices, blank=True)
    id_name_matches = models.BooleanField(default=False)
    signature_image = models.ImageField(storage=private_storage_factory, upload_to="signatures/%Y/%m/", blank=True)
    proof_photo = models.ImageField(storage=private_storage_factory, upload_to="handover_proof/%Y/%m/", blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    receipt_code = models.CharField(max_length=14, unique=True, default=_receipt, editable=False)
    notes = models.CharField(max_length=500, blank=True)
    cancelled_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(status="completed") | (models.Q(completed_at__isnull=False) & models.Q(id_name_matches=True)),
                name="completed_handover_has_evidence"),
        ]

    def __str__(self):
        return f"Handover {self.receipt_code} for {self.claim.reference}"


class DisposalRecord(models.Model):
    class Method(models.TextChoices):
        DONATED = "donated", "Donated to charity"
        RECYCLED = "recycled", "Recycled"
        DESTROYED = "destroyed", "Securely destroyed (e.g. ID cards, data devices)"
        HANDED_TO_POLICE = "police", "Handed to police"
        UNIVERSITY_USE = "university_use", "Retained for university use"

    item = models.OneToOneField("lostfound.Item", on_delete=models.PROTECT, related_name="disposal")
    method = models.CharField(max_length=20, choices=Method.choices)
    reason = models.TextField(max_length=1000)
    recipient = models.CharField(max_length=150, blank=True, help_text="Receiving organisation, if any.")
    evidence = models.ImageField(storage=private_storage_factory, upload_to="disposal/%Y/%m/", blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    disposed_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Disposal of {self.item.reference} ({self.get_method_display()})"
