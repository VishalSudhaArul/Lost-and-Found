"""Acceptance criterion: match suggestions are returned for at least 70% of
seeded matching pairs, without flooding owners with false matches."""
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from lostfound.models import Item, ItemCategory, Location, MatchSuggestion, Notification
from lostfound.services import matching

from .base import LFTestCase

PAIRS = [
    # (category, location, lost title/desc, found title/desc, colour)
    ("backpacks", "hall", "Black Wildcraft backpack", "Black backpack, Wildcraft logo, two zips", "black"),
    ("phones", "canteen", "Samsung phone in blue case", "Blue cased Samsung smartphone", "blue"),
    ("backpacks", "library", "Grey laptop bag", "Grey laptop bag with shoulder strap", "grey"),
    ("phones", "hall", "iPhone with cracked screen", "Apple iPhone, screen cracked", "black"),
    ("backpacks", "canteen", "Red handbag", "Small red handbag", "red"),
    ("phones", "library", "OnePlus phone green", "Green OnePlus mobile phone", "green"),
    ("backpacks", "hall", "Navy blue school bag", "Navy school bag with keychain", "navy"),
    ("phones", "canteen", "Redmi phone white", "White Redmi phone", "white"),
    ("backpacks", "library", "Brown leather satchel", "Leather satchel, brown", "brown"),
    ("phones", "hall", "Pixel phone pink case", "Google Pixel in a pink case", "pink"),
]


class MatchingTests(LFTestCase):
    def setUp(self):
        super().setUp()
        self.cats = {"backpacks": self.backpacks, "phones": self.phones}
        self.locs = {"hall": self.hall, "library": self.library, "canteen": self.canteen}

    def seed(self):
        now = timezone.now()
        pairs = []
        for i, (cat, loc, lost_t, found_t, colour) in enumerate(PAIRS):
            lost = Item.objects.create(report_type="lost", title=lost_t, description=lost_t, category=self.cats[cat],
                                       location=self.locs[loc], occurred_at=now - timedelta(days=2, hours=i),
                                       reporter=self.student, attributes={"colour": colour})
            found = Item.objects.create(report_type="found", title=found_t, description=found_t, category=self.cats[cat],
                                        location=self.locs[loc], occurred_at=now - timedelta(days=1, hours=i),
                                        reporter=self.finder, attributes={"colour": colour}, status="in_custody")
            pairs.append((lost, found))
        # Distractors: other categories, far away in time
        other = ItemCategory.objects.create(name="Keys")
        far = Location.objects.create(name="Hostel", parent=None)
        for i in range(10):
            Item.objects.create(report_type="found", title=f"Key ring {i}", description="Bunch of keys", category=other,
                                location=far, occurred_at=now - timedelta(days=25), reporter=self.finder, status="in_custody")
        return pairs

    def test_at_least_70_percent_of_seeded_pairs_are_suggested(self):
        pairs = self.seed()
        matching.refresh_all()
        hits = sum(MatchSuggestion.objects.filter(lost_item=l, found_item=f).exists() for l, f in pairs)
        recall = hits / len(pairs)
        self.assertGreaterEqual(recall, 0.7, f"recall {recall:.0%}")
        # Precision: distractors (Keys) are never suggested
        self.assertFalse(MatchSuggestion.objects.filter(found_item__category__name="Keys").exists())

    def test_best_suggestion_for_each_lost_item_is_its_true_pair(self):
        pairs = self.seed()
        matching.refresh_all()
        correct = 0
        for lost, found in pairs:
            top = MatchSuggestion.objects.filter(lost_item=lost).order_by("-score").first()
            correct += bool(top and top.found_item_id == found.pk)
        self.assertGreaterEqual(correct / len(pairs), 0.7)

    def test_components_explain_score_and_owner_notified_once(self):
        lost, found = self.seed()[0]
        matching.refresh_all()
        matching.refresh_all()
        s = MatchSuggestion.objects.get(lost_item=lost, found_item=found)
        self.assertEqual(set(s.components["factors"]), {"category", "text", "attributes", "location", "time"})
        self.assertEqual(s.components["factors"]["category"], 1.0)
        self.assertEqual(len(matching.explain(s)), 5)
        self.assertEqual(Notification.objects.filter(user=self.student, dedupe_key=f"match:{lost.pk}:{found.pk}").count(), 1)

    def test_threshold_is_configurable(self):
        pairs = self.seed()
        self.cfg.match_threshold = 0.99
        self.cfg.save()
        matching.refresh_all()
        self.assertGreater(len(pairs), MatchSuggestion.objects.count())
        self.assertFalse(MatchSuggestion.objects.filter(score__lt=0.99).exists())

    def test_dismissed_suggestions_stay_dismissed(self):
        lost, found = self.seed()[0]
        matching.refresh_all()
        MatchSuggestion.objects.filter(lost_item=lost, found_item=found).update(status="dismissed")
        matching.refresh_all()
        self.assertEqual(MatchSuggestion.objects.get(lost_item=lost, found_item=found).status, "dismissed")

    def test_found_before_lost_scores_zero_time(self):
        lost, found = self.seed()[0]
        found.occurred_at = lost.occurred_at - timedelta(days=5)
        self.assertEqual(matching.time_score(lost, found, 30), 0.0)

    def test_matching_uses_public_fields_only(self):
        import inspect
        src = inspect.getsource(matching)
        self.assertNotIn("PrivateDetail.vault", src)
        self.assertNotIn("decrypt", src)

    def test_python_trigram_matches_pg_trgm_definition(self):
        self.assertAlmostEqual(matching.trigram_similarity("word", "two words"), 0.36363636, places=5)

    def test_python_fallback_agrees_with_pg_trgm(self):
        with connection.cursor() as cur:
            for a, b in [("black backpack", "black wildcraft backpack"), ("phone", "iphone cracked"), ("abc", "xyz")]:
                cur.execute("SELECT similarity(%s, %s)", [a, b])
                self.assertAlmostEqual(cur.fetchone()[0], matching.trigram_similarity(a, b), places=4)

    def test_postgres_board_search_handles_stemming_and_typos(self):
        from lostfound.services import public
        self.seed()
        titles = [i.title for i in public.to_public_items(public.board_queryset({"q": "backpacks"}))]
        self.assertIn("Black backpack, Wildcraft logo, two zips", titles)
        titles = [i.title for i in public.to_public_items(public.board_queryset({"q": "samsng"}))]
        self.assertTrue(any("Samsung" in t for t in titles), titles)
