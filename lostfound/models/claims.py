import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from ..services.storage import private_storage_factory


def _claim_ref():
    return "C-" + secrets.token_hex(4).upper()


class Claim(models.Model):
    class Status(models.TextChoices):
        PENDING_VERIFICATION = "pending_verification", "Pending verification"
        UNDER_REVIEW = "under_review", "Pending review"
        INFO_REQUESTED = "info_requested", "More information requested"
        APPROVED = "approved", "Approved — awaiting handover"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        COMPLETED = "completed", "Returned to owner"

    class Verification(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"

    ACTIVE_STATUSES = (Status.PENDING_VERIFICATION, Status.UNDER_REVIEW, Status.INFO_REQUESTED, Status.APPROVED)
    DECIDED_STATUSES = (Status.APPROVED, Status.COMPLETED)

    reference = models.CharField(max_length=12, unique=True, default=_claim_ref, editable=False)
    item = models.ForeignKey("lostfound.Item", on_delete=models.PROTECT, related_name="claims")
    claimant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="claims")
    lost_report = models.ForeignKey(
        "lostfound.Item", null=True, blank=True, on_delete=models.SET_NULL, related_name="claims_as_lost_report",
        help_text="The claimant's own lost report, if any.")
    statement = models.TextField(max_length=1500, help_text="What the item looks like, in the claimant's words. Visible to the claimant and security only.")
    lost_where_when = models.CharField(max_length=300, blank=True, help_text="Where and roughly when it was lost.")
    extra_info = models.TextField(max_length=1500, blank=True, help_text="Anything else the claimant remembers.")
    attachment = models.ImageField(storage=private_storage_factory, upload_to="claims/%Y/%m/", blank=True,
                                   help_text="Optional supporting photo; private to security staff.")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING_VERIFICATION, db_index=True)
    verification_status = models.CharField(max_length=12, choices=Verification.choices, default=Verification.NOT_STARTED)
    decision_reason = models.TextField(blank=True, help_text="Visible to the claimant and security only.")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One live claim per person per item.
            models.UniqueConstraint(
                fields=["item", "claimant"],
                condition=models.Q(status__in=["pending_verification", "under_review", "info_requested", "approved"]),
                name="uniq_active_claim_per_claimant"),
            # Never two approved/completed claims for the same item.
            models.UniqueConstraint(
                fields=["item"], condition=models.Q(status__in=["approved", "completed"]),
                name="uniq_approved_claim_per_item"),
        ]
        indexes = [models.Index(fields=["status", "-created_at"], name="claim_status_created")]

    def __str__(self):
        return f"{self.reference} on {self.item.reference}"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES


class ClaimMessage(models.Model):
    """Conversation between security staff and the claimant (requests for more information)."""
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    from_staff = models.BooleanField()
    body = models.TextField(max_length=1500)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"Message on {self.claim.reference}"


class VerificationChallenge(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Awaiting answers"
        PASSED = "passed", "Passed"
        LOCKED = "locked", "Locked (attempts exhausted)"
        CANCELLED = "cancelled", "Cancelled"

    claim = models.OneToOneField(Claim, on_delete=models.PROTECT, related_name="challenge")
    # Which private details were drawn, in presentation order.
    detail_ids = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    max_attempts = models.PositiveSmallIntegerField()
    attempts_used = models.PositiveSmallIntegerField(default=0)
    pass_threshold = models.FloatField()
    best_score = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(attempts_used__lte=models.F("max_attempts")),
                                   name="challenge_attempts_within_limit"),
        ]

    def __str__(self):
        return f"Challenge for {self.claim.reference}"

    @property
    def attempts_remaining(self):
        return max(0, self.max_attempts - self.attempts_used)


class VerificationAttempt(models.Model):
    """History of answers. Answers are stored encrypted; per-question scores
    are numbers only and are shown to security staff, never to claimants."""

    challenge = models.ForeignKey(VerificationChallenge, on_delete=models.PROTECT, related_name="attempts")
    responses_ciphertext = models.TextField()
    question_scores = models.JSONField(default=dict)
    score = models.FloatField()
    passed = models.BooleanField()
    submitted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["submitted_at"]

    def __str__(self):
        return f"Attempt {self.pk} on {self.challenge}"
