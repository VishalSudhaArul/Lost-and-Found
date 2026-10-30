"""Remove the rows created by the old ``seed_demo`` command, and nothing else.

A row counts as seeded only when *all* of its provenance markers match what
``seed_demo`` wrote: seed username + surname "(sample)" + ``<username>@example.edu``
for accounts, and a "[Sample] " title reported by one of those accounts for items.
Categories and locations are kept because real reports use them.

Runs as a dry run unless ``--apply`` is given. Everything happens in one
transaction; if any real record would change, it rolls back."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from lostfound.models import (AuditEntry, Claim, ClaimMessage, CustodyRecord, Handover, Item, MatchSuggestion,
                              PointsLedgerEntry, PrivateDetail)

SEED_USERNAMES = ("student1", "student2", "staff1", "security1", "campusadmin", "sysadmin")


class Rollback(Exception):
    pass


def seed_users():
    User = get_user_model()
    q = Q()
    for name in SEED_USERNAMES:
        q |= Q(username=name, email=f"{name}@example.edu")
    return User.objects.filter(q, last_name="(sample)", is_superuser=False)


def snapshot(users, items):
    """Fingerprint of every record that is *not* seeded."""
    User = get_user_model()
    return {
        "users": sorted(User.objects.exclude(pk__in=users).values_list("pk", "username", "email", "password")),
        "items": sorted(Item.objects.exclude(pk__in=items).values_list("pk", "status", "reporter_id")),
        "claims": sorted(Claim.objects.values_list("pk", "status", "claimant_id", "item_id")),
        "messages": ClaimMessage.objects.count(),
        "custody": sorted(CustodyRecord.objects.exclude(item__in=items).values_list("pk")),
        "handovers": sorted(Handover.objects.values_list("pk", "status")),
        "ledger": sorted(PointsLedgerEntry.objects.values_list("pk", "user_id", "points")),
        "details": sorted(PrivateDetail._base_manager.exclude(item__in=items).values_list("pk")),
    }


class Command(BaseCommand):
    help = "Remove rows created by the old seed_demo command (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Actually delete (default is a dry run).")

    def handle(self, *args, **opts):
        users = list(seed_users().values_list("pk", flat=True))
        items = list(Item.objects.filter(title__startswith="[Sample] ", reporter_id__in=users).values_list("pk", flat=True))
        self.stdout.write(f"Seeded accounts: {sorted(seed_users().values_list('username', flat=True))}")
        self.stdout.write(f"Seeded items: {sorted(Item.objects.filter(pk__in=items).values_list('title', flat=True))}")

        # Anything real that still points at a seeded row makes the split ambiguous: stop.
        blockers = {
            "claims on seeded items or by seeded accounts": Claim.objects.filter(Q(item_id__in=items) | Q(claimant_id__in=users) | Q(decided_by_id__in=users)).count(),
            "real items reported by seeded accounts": Item.objects.filter(reporter_id__in=users).exclude(pk__in=items).count(),
            "custody records on real items by seeded accounts": CustodyRecord.objects.filter(accepted_by_id__in=users).exclude(item_id__in=items).count(),
            "handovers involving seeded accounts": Handover.objects.filter(Q(officer_id__in=users) | Q(claim__claimant_id__in=users)).count(),
            "reward entries for seeded accounts": PointsLedgerEntry.objects.filter(Q(user_id__in=users) | Q(awarded_by_id__in=users)).count(),
            "messages by seeded accounts": ClaimMessage.objects.filter(author_id__in=users).count(),
            "audit entries by seeded accounts about real records": AuditEntry.objects.filter(actor_id__in=users).exclude(
                target_type="Item", target_id__in=[str(i) for i in items]).count(),
        }
        bad = {k: v for k, v in blockers.items() if v}
        if bad:
            raise CommandError(f"Stopped: seeded and real data are linked ({bad}). Nothing was deleted.")

        before = snapshot(users, items)
        try:
            with transaction.atomic():
                counts = {
                    "match suggestions": MatchSuggestion.objects.filter(Q(lost_item_id__in=items) | Q(found_item_id__in=items)).delete()[0],
                    "private details": PrivateDetail._base_manager.filter(item_id__in=items).delete()[0],
                    "custody records": CustodyRecord.objects.filter(item_id__in=items).delete()[0],
                    "audit entries (seed actions)": AuditEntry.objects.filter(actor_id__in=users).delete()[0],
                    "items": Item.objects.filter(pk__in=items).delete()[0],
                    "accounts (+ their profiles and inboxes)": get_user_model().objects.filter(pk__in=users).delete()[0],
                }
                if snapshot(users, items) != before:
                    raise CommandError("A real record would have changed; rolled back.")
                for k, v in counts.items():
                    self.stdout.write(f"  {k}: {v} row(s)")
                if not opts["apply"]:
                    raise Rollback
        except Rollback:
            self.stdout.write(self.style.WARNING("Dry run only; nothing was deleted. Re-run with --apply."))
            return
        self.stdout.write(self.style.SUCCESS("Seeded demo data removed. Real records unchanged."))
