from django.core.management.base import BaseCommand

from lostfound.services import disposal


class Command(BaseCommand):
    help = "Warn about, queue and escalate unclaimed items by retention policy (schedule daily)."

    def handle(self, *args, **options):
        summary = disposal.run_ageing_check()
        self.stdout.write(self.style.SUCCESS(
            "Ageing check: {warned} warned, {moved_to_review} moved to disposal review, "
            "{protected_by_claims} protected by active claims, {escalated} escalated.".format(**summary)))
