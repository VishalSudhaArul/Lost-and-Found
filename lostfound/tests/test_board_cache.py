"""Public board: cached with Django's cache framework, invalidated on change, never caching private data."""
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from lostfound.models import Item

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase


@override_settings(BOARD_CACHE_SECONDS=60)
class BoardCacheTests(LFTestCase):
    def board(self, query=""):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse("board") + query)
        return r, len(ctx.captured_queries)

    def test_second_request_is_served_from_cache(self):
        self.found_item(title="Blue bottle")
        first, q1 = self.board()
        second, q2 = self.board()
        self.assertContains(second, "Blue bottle")
        self.assertLess(q2, q1)

    def test_any_item_change_shows_up_immediately(self):
        item = self.found_item(title="Blue bottle")
        self.board()
        self.found_item(title="Red umbrella")
        self.assertContains(self.board()[0], "Red umbrella")
        item.is_hidden = True
        item.save()
        self.assertNotContains(self.board()[0], "Blue bottle")

    def test_cached_entries_hold_only_redacted_data(self):
        self.found_item(title="Black backpack")
        self.board()
        self.board("?q=backpack")
        self.client.get(reverse("board_json"))
        dump = repr([cache.get(k) for k in cache._cache.keys()])  # locmem internals: test-only inspection
        for secret in (SECRET_ANSWER, SECRET_SERIAL, "What sticker", "What is the serial"):
            self.assertNotIn(secret, dump)
        self.assertNotContains(self.board("?q=zebra")[0], SECRET_ANSWER)

    def test_filters_and_pages_are_cached_separately(self):
        for i in range(14):
            self.found_item(title=f"Bag number {i}", details=False)
        r1 = self.board()[0]
        r2 = self.board("?page=2")[0]
        self.assertContains(r1, 'rel="next"')  # hook used by infinite scroll; plain link without JS
        self.assertContains(r2, "Page 2 of 2")
        self.assertEqual(Item.objects.count(), 14)

    @override_settings(BOARD_CACHE_SECONDS=0)
    def test_caching_can_be_switched_off(self):
        self.found_item(title="Blue bottle")
        _, q1 = self.board()
        _, q2 = self.board()
        self.assertEqual(q1, q2)
