from django.db import models


class MatchSuggestion(models.Model):
    class Status(models.TextChoices):
        SUGGESTED = "suggested", "Suggested"
        DISMISSED = "dismissed", "Dismissed"
        CLAIMED = "claimed", "Claim started"

    lost_item = models.ForeignKey("lostfound.Item", on_delete=models.CASCADE, related_name="match_suggestions_as_lost")
    found_item = models.ForeignKey("lostfound.Item", on_delete=models.CASCADE, related_name="match_suggestions_as_found")
    score = models.FloatField(db_index=True)
    components = models.JSONField(default=dict, help_text="Per-factor scores explaining the match (no private data).")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUGGESTED)
    owner_notified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score"]
        constraints = [
            models.UniqueConstraint(fields=["lost_item", "found_item"], name="uniq_match_pair"),
            models.CheckConstraint(condition=models.Q(score__gte=0) & models.Q(score__lte=1), name="match_score_range"),
        ]

    def __str__(self):
        return f"{self.lost_item_id} ↔ {self.found_item_id} ({self.score:.2f})"

    @property
    def percent(self):
        return round(self.score * 100)
