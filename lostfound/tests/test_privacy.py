"""Acceptance criterion: private details never appear in any public
response — proven exhaustively across every item state."""
import json

from django.db import connection
from django.urls import reverse

from lostfound.models import Item, PrivateDetail
from lostfound.services import public

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase

SECRETS = [SECRET_ANSWER, "zebra", SECRET_SERIAL, "77QX", "What sticker is on the front?", "serial number"]


class PrivateDetailLeakTests(LFTestCase):
    def assert_no_secrets(self, content, where, echoed=""):
        text = content.decode() if isinstance(content, bytes) else content
        if echoed:  # the visitor's own search term is echoed back in the search box
            text = text.replace(f'value="{echoed}"', "").replace(f'"q": "{echoed}"', "")
        for s in SECRETS:
            self.assertNotIn(s.lower(), text.lower(), f"private detail '{s}' leaked in {where}")

    def public_responses(self, item):
        urls = [reverse("home"), reverse("board"), reverse("board") + "?q=backpack", reverse("board") + "?q=zebra",
                reverse("board") + "?q=" + SECRET_SERIAL, reverse("board") + "?type=found&sort=relevance&q=black",
                reverse("board_json"), reverse("board_json") + "?q=zebra", reverse("item_detail", args=[item.pk]),
                reverse("locations_json")]
        for url in urls:
            response = self.client.get(url)
            self.assertIn(response.status_code, (200, 404), url)
            yield url, response

    def test_no_leak_in_any_item_state_anonymous_and_logged_in(self):
        item = self.found_item()
        for status in Item.Status.values:
            Item.objects.filter(pk=item.pk).update(status=status)
            for user in (None, self.student, self.finder):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                for url, response in self.public_responses(item):
                    echoed = url.split("q=")[-1].split("&")[0] if "q=" in url else ""
                    self.assert_no_secrets(response.content, f"{url} [{status}, {user}]", echoed=echoed)

    def test_searching_for_a_private_answer_finds_nothing(self):
        self.found_item()
        for q in ("zebra", "purple sticker", SECRET_SERIAL, "77QX"):
            self.assertEqual(self.client.get(reverse("board"), {"q": q}).context["page"].paginator.count, 0, q)
            self.assertEqual(json.loads(self.client.get(reverse("board_json"), {"q": q}).content)["count"], 0, q)

    def test_json_contains_only_allow_listed_fields(self):
        self.found_item()
        data = json.loads(self.client.get(reverse("board_json")).content)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(set(data["results"][0]), {
            "id", "reference", "report_type", "title", "description", "category", "location", "occurred_at", "status",
            "status_label", "photo_url", "attributes", "created_at"})
        self.assertNotIn("finder", json.dumps(data))  # reporter identity is not public either

    def test_public_templates_receive_dtos_not_models(self):
        item = self.found_item()
        response = self.client.get(reverse("item_detail", args=[item.pk]))
        self.assertIsInstance(response.context["item"], public.PublicItem)
        response = self.client.get(reverse("board"))
        for obj in response.context["items"]:
            self.assertIsInstance(obj, public.PublicItem)

    def test_item_has_no_reverse_accessor_to_private_details(self):
        item = self.found_item()
        self.assertFalse(hasattr(item, "privatedetail_set"))
        self.assertFalse(hasattr(PrivateDetail, "objects"))

    def test_private_details_are_encrypted_at_rest(self):
        item = self.found_item()
        with connection.cursor() as cur:
            cur.execute("SELECT question_ciphertext, answer_ciphertext FROM lostfound_privatedetail WHERE item_id = %s", [item.pk])
            rows = cur.fetchall()
        self.assertEqual(len(rows), 2)
        for q, a in rows:
            self.assertNotIn("zebra", q + a)
            self.assertNotIn("77QX", q + a)
        self.assertEqual(str(PrivateDetail.vault.filter(item=item).first()).count("zebra"), 0)

    def test_hidden_and_terminal_items_are_not_listed(self):
        item = self.found_item()
        Item.objects.filter(pk=item.pk).update(is_hidden=True)
        self.assertEqual(self.client.get(reverse("item_detail", args=[item.pk])).status_code, 404)
        Item.objects.filter(pk=item.pk).update(is_hidden=False, status=Item.Status.RETURNED)
        self.assertEqual(self.client.get(reverse("item_detail", args=[item.pk])).status_code, 404)

    def test_notifications_never_contain_private_details(self):
        from lostfound.models import Notification
        claim = self.verified_claim()
        for n in Notification.objects.all():
            self.assert_no_secrets(n.title + n.message, f"notification {n.pk}")
        self.assertTrue(claim.pk)

    def test_audit_log_never_contains_answers(self):
        from lostfound.models import AuditEntry
        self.verified_claim()
        for e in AuditEntry.objects.all():
            self.assert_no_secrets(e.summary + json.dumps(e.metadata), f"audit {e.action}")
