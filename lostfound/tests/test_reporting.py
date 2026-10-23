import io
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from PIL import Image

from lostfound.models import Item, PrivateDetail

from .base import LFTestCase, jpeg_bytes, upload


class ReportingTests(LFTestCase):
    def payload(self, **kw):
        data = {"title": "Blue water bottle", "category": self.backpacks.pk, "description": "Blue bottle with a dent.",
                "occurred_at": (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "location": self.hall.pk, "location_note": "", "attr_colour": "blue"}
        data.update(kw)
        return data

    def found_payload(self, **kw):
        data = self.payload(**{"private-TOTAL_FORMS": "3", "private-INITIAL_FORMS": "0", "private-MIN_NUM_FORMS": "1",
                               "private-MAX_NUM_FORMS": "5", "private-0-kind": "mark",
                               "private-0-question": "What is written on the cap?", "private-0-answer": "go team",
                               "private-0-match_mode": "keywords"})
        data.update(kw)
        return data

    def test_anonymous_cannot_report(self):
        r = self.client.get(reverse("report_item", args=["lost"]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])

    def test_lost_report_saves_with_attributes(self):
        self.client.force_login(self.student)
        r = self.client.post(reverse("report_item", args=["lost"]), self.payload())
        item = Item.objects.get()
        self.assertRedirects(r, reverse("report_detail", args=[item.pk]))
        self.assertEqual((item.report_type, item.status, item.reporter), ("lost", "open", self.student))
        self.assertEqual(item.attributes, {"colour": "blue"})

    def test_found_report_requires_and_stores_private_detail(self):
        self.client.force_login(self.finder)
        r = self.client.post(reverse("report_item", args=["found"]), self.found_payload(**{"private-0-question": "", "private-0-answer": ""}))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Item.objects.exists())
        r = self.client.post(reverse("report_item", args=["found"]), self.found_payload())
        item = Item.objects.get()
        detail = PrivateDetail.vault.get(item=item)
        self.assertEqual(detail.decrypt_answer(), "go team")
        self.assertNotIn("go team", detail.answer_ciphertext)

    def test_question_may_not_contain_answer(self):
        self.client.force_login(self.finder)
        r = self.client.post(reverse("report_item", args=["found"]), self.found_payload(**{"private-0-question": "Is it go team?"}))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Item.objects.exists())

    def test_future_date_rejected(self):
        self.client.force_login(self.student)
        future = (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        r = self.client.post(reverse("report_item", args=["lost"]), self.payload(occurred_at=future))
        self.assertContains(r, "cannot be in the future")
        self.assertFalse(Item.objects.exists())

    def test_invalid_choice_attribute_rejected(self):
        from lostfound.models import CategoryAttribute
        CategoryAttribute.objects.create(category=self.bags, key="size", label="Size", kind="choice", choices="S,M,L")
        self.client.force_login(self.student)
        r = self.client.post(reverse("report_item", args=["lost"]), self.payload(attr_size="XXL"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Item.objects.exists())

    def test_non_image_upload_rejected_even_with_image_name(self):
        self.client.force_login(self.student)
        bad = upload("evil.jpg", b"<?php system($_GET['x']); ?>", "image/jpeg")
        r = self.client.post(reverse("report_item", args=["lost"]), {**self.payload(), "photo": bad})
        self.assertContains(r, "not a valid image")
        self.assertFalse(Item.objects.exists())

    def test_disallowed_format_rejected(self):
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="GIF")
        self.client.force_login(self.student)
        r = self.client.post(reverse("report_item", args=["lost"]), {**self.payload(), "photo": upload("a.gif", buf.getvalue(), "image/gif")})
        self.assertContains(r, "JPEG, PNG or WebP")

    def test_oversized_upload_rejected(self):
        self.client.force_login(self.student)
        with self.settings(MAX_IMAGE_UPLOAD_BYTES=100):
            r = self.client.post(reverse("report_item", args=["lost"]), {**self.payload(), "photo": upload()})
        self.assertContains(r, "smaller than")

    def test_photo_is_reencoded_without_exif_and_renamed(self):
        self.client.force_login(self.student)
        raw = jpeg_bytes((3000, 2000), exif_gps=True)
        self.assertIn(b"TestCam", raw)
        self.client.post(reverse("report_item", args=["lost"]), {**self.payload(), "photo": upload("IMG_0001.jpg", raw)})
        item = Item.objects.get()
        self.assertNotIn("IMG_0001", item.photo.name)
        with item.photo.open("rb") as fh:
            stored = fh.read()
        self.assertNotIn(b"TestCam", stored)
        img = Image.open(io.BytesIO(stored))
        self.assertEqual(len(img.getexif()), 0)
        self.assertLessEqual(max(img.size), 1600)

    def test_reporter_can_close_own_open_report_only(self):
        item = self.lost_item()
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("close_report", args=[item.pk])).status_code, 404)
        self.client.force_login(self.student)
        self.client.post(reverse("close_report", args=[item.pk]))
        item.refresh_from_db()
        self.assertEqual(item.status, "closed")
