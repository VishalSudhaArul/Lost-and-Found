"""Creates clearly-labelled SAMPLE data for local demos. Not campus records."""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from lostfound import roles
from lostfound.models import CategoryAttribute, Item, ItemCategory, Location, UserProfile
from lostfound.services import custody, matching, verification

User = get_user_model()

CATEGORIES = {
    "Electronics": ["Phones", "Laptops & tablets", "Earphones & chargers", "Calculators"],
    "Bags": ["Backpacks", "Handbags & pouches"],
    "Documents & cards": ["ID cards", "Wallets"],
    "Clothing": ["Jackets", "Caps & accessories"],
    "Bottles & lunchboxes": [],
    "Keys": [],
    "Books & stationery": [],
}
ATTRS = {"Electronics": [("brand", "Brand"), ("colour", "Colour")], "Bags": [("colour", "Colour"), ("brand", "Brand")],
         "Clothing": [("colour", "Colour"), ("size", "Size")], "Bottles & lunchboxes": [("colour", "Colour")]}
# Placeholder campus structure. Replace with your campus via Manage › Campus locations.
LOCATIONS = {
    "Academic Block A": ["Ground floor", "First floor"],
    "Central Library": ["Reading hall", "Ground floor"],
    "Food Court": [],
    "Sports Complex": [],
    "Hostel Zone": [],
}


class Command(BaseCommand):
    help = "Create labelled sample categories, locations, users and items for a local demo."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for demo accounts (default: random, printed once).")
        parser.add_argument("--no-items", action="store_true")

    def handle(self, *args, **opts):
        if Item.objects.exists() and not opts["no_items"]:
            raise CommandError("Items already exist; run with --no-items or on an empty database.")
        password = opts["password"] or secrets.token_urlsafe(10)
        for parent, children in CATEGORIES.items():
            p, _ = ItemCategory.objects.get_or_create(name=parent, parent=None)
            for key, label in ATTRS.get(parent, []):
                CategoryAttribute.objects.get_or_create(category=p, key=key, defaults={"label": label})
            for c in children:
                ItemCategory.objects.get_or_create(name=c, parent=p)
        campus, _ = Location.objects.get_or_create(name="Main Campus (sample)", parent=None, defaults={"kind": "area"})
        for b, floors in LOCATIONS.items():
            bl, _ = Location.objects.get_or_create(name=b, parent=campus, defaults={"kind": "building"})
            for f in floors:
                Location.objects.get_or_create(name=f, parent=bl, defaults={"kind": "floor"})
        office, _ = Location.objects.get_or_create(name="Security Office", parent=campus,
                                                   defaults={"kind": "building", "is_custody_point": True})
        users = {}
        for username, first, group in [("student1", "Asha", roles.MEMBERS), ("student2", "Ravi", roles.MEMBERS),
                                       ("staff1", "Meera", roles.MEMBERS), ("security1", "Karthik", roles.SECURITY),
                                       ("campusadmin", "Priya", roles.CAMPUS_ADMIN), ("sysadmin", "Arun", roles.SYSTEM_ADMIN)]:
            u, created = User.objects.get_or_create(username=username, defaults={"first_name": first, "last_name": "(sample)",
                                                                                 "email": f"{username}@example.edu"})
            if created:
                u.set_password(password)
                u.save()
                UserProfile.objects.get_or_create(user=u, defaults={"member_type": "staff" if "staff" in username else "student"})
            u.groups.add(Group.objects.get(name=group))
            if group != roles.MEMBERS:
                u.groups.add(Group.objects.get(name=roles.MEMBERS))
            users[username] = u
        self.stdout.write(f"Demo accounts: {', '.join(users)} — password: {password}")
        if opts["no_items"]:
            return
        now = timezone.now()
        cat = {c.name: c for c in ItemCategory.objects.all()}
        loc = {l.name: l for l in Location.objects.all()}
        def item(kind, title, desc, c, l, hours_ago, reporter, attrs=None):
            return Item.objects.create(report_type=kind, title=f"[Sample] {title}", description=desc, category=cat[c],
                                       location=loc[l], occurred_at=now - timedelta(hours=hours_ago), reporter=reporter,
                                       attributes=attrs or {})
        found = item("found", "Black backpack with laptop sleeve", "Black backpack left on a bench.", "Backpacks", "Reading hall", 20,
                     users["staff1"], {"colour": "black", "brand": "Wildcraft"})
        verification.add_private_detail(found, users["staff1"], "contents", "What is inside the front pocket?", "a blue calculator and a red pen")
        verification.add_private_detail(found, users["staff1"], "mark", "Describe the keychain attached to the zip", "a small rubber football")
        custody.receive_item(found.pk, users["security1"], office)
        f2 = item("found", "Steel water bottle", "Silver insulated bottle.", "Bottles & lunchboxes", "Food Court", 5, users["student2"], {"colour": "silver"})
        verification.add_private_detail(f2, users["student2"], "engraving", "What is written on the bottom?", "AK 2025", "exact")
        item("lost", "Black Wildcraft backpack", "Lost my black backpack in the library reading hall.", "Backpacks", "Reading hall", 22,
             users["student1"], {"colour": "black", "brand": "Wildcraft"})
        item("lost", "Wireless earphones case", "White earbuds case.", "Earphones & chargers", "Ground floor", 30, users["student2"], {"colour": "white"})
        n = matching.refresh_all()
        self.stdout.write(self.style.SUCCESS(f"Sample items created; {n} match suggestion(s)."))
