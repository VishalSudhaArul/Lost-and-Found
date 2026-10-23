"""Private identifying details — the core trust mechanism of P09.

Design decisions that make leakage structurally hard:
* A separate model: nothing on ``Item`` contains private data.
* ``related_name="+"``: there is NO ``item.privatedetail_set`` accessor, so a
  template or serializer working from an Item cannot reach these rows.
* The only manager is named ``vault`` (there is no ``objects``) and decrypted
  answers are only produced inside ``lostfound.services.verification``, which
  checks permissions and writes an audit entry for staff access.
* Question and answer are stored as Fernet ciphertext only.
"""
from django.conf import settings
from django.db import models

from ..services import crypto


class PrivateDetailManager(models.Manager):
    def for_item(self, item):
        return self.get_queryset().filter(item=item, is_active=True)


class PrivateDetail(models.Model):
    class Kind(models.TextChoices):
        MARK = "mark", "Distinguishing mark"
        CONTENTS = "contents", "Unadvertised contents"
        ENGRAVING = "engraving", "Engraving / inscription"
        IDENTIFIER = "identifier", "Serial number / unique identifier"
        OTHER = "other", "Other detail only the owner would know"

    class MatchMode(models.TextChoices):
        EXACT = "exact", "Exact (ignores case, spaces and punctuation)"
        KEYWORDS = "keywords", "Key words (most key words must appear)"
        FUZZY = "fuzzy", "Close match (tolerates typos)"

    item = models.ForeignKey("lostfound.Item", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    question_ciphertext = models.TextField()
    answer_ciphertext = models.TextField()
    match_mode = models.CharField(max_length=10, choices=MatchMode.choices, default=MatchMode.KEYWORDS)
    weight = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    vault = PrivateDetailManager()

    class Meta:
        default_manager_name = "vault"
        base_manager_name = "vault"
        indexes = [models.Index(fields=["item", "is_active"], name="privatedetail_item_active")]

    def __str__(self):
        # Never include decrypted content in string representations.
        return f"Private detail #{self.pk} ({self.get_kind_display()}) for item {self.item_id}"

    def set_question(self, text):
        self.question_ciphertext = crypto.encrypt_text(text)

    def set_answer(self, text):
        self.answer_ciphertext = crypto.encrypt_text(text)

    def decrypt_question(self):
        return crypto.decrypt_text(self.question_ciphertext)

    def decrypt_answer(self):
        return crypto.decrypt_text(self.answer_ciphertext)
