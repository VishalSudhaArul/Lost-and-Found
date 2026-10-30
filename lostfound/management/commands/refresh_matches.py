from django.core.management.base import BaseCommand

from lostfound.services import matching


class Command(BaseCommand):
    help = "Recompute match suggestions for all open lost reports (schedule nightly)."

    def handle(self, *args, **options):
        engine = "pg_trgm"
        count = matching.refresh_all()
        self.stdout.write(self.style.SUCCESS(f"Match refresh complete using {engine}: {count} suggestion(s) at or above threshold."))
