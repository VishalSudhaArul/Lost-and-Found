from django.core.management.base import BaseCommand
from django.db import transaction

from lostfound.models import PrivateDetail, VerificationAttempt
from lostfound.services import crypto


class Command(BaseCommand):
    help = ("Re-encrypt private data with the first key in FIELD_ENCRYPTION_KEYS. Put the new key first, "
            "keep the old key(s) after it, run this, then remove the old keys.")

    @transaction.atomic
    def handle(self, *args, **options):
        n = 0
        for d in PrivateDetail.vault.all():
            PrivateDetail.vault.filter(pk=d.pk).update(question_ciphertext=crypto.rotate(d.question_ciphertext),
                                                       answer_ciphertext=crypto.rotate(d.answer_ciphertext))
            n += 1
        for a in VerificationAttempt.objects.all():
            VerificationAttempt.objects.filter(pk=a.pk).update(responses_ciphertext=crypto.rotate(a.responses_ciphertext))
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted {n} record(s)."))
