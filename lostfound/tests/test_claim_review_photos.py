"""Claim review shows the finder's original photo and the claimant's private photo separately."""
from django.core.files.base import ContentFile
from django.urls import reverse

from lostfound.services import claims, images

from .base import LFTestCase, jpeg_bytes, upload


class ClaimReviewPhotoTests(LFTestCase):
    def setUp(self):
        super().setUp()
        self.item = self.found_item(title="Silver laptop")
        self.item.photo.save("finder.jpg", ContentFile(jpeg_bytes(), name="finder.jpg"))
        self.claim = claims.submit_claim(self.item, self.student, "Silver laptop with a sticker", where_when="Library",
                                         attachment=images.process_upload(upload()))
        self.url = reverse("security_claim", args=[self.claim.pk])

    def test_staff_see_finder_photo_and_claimant_photo_separately(self):
        self.client.force_login(self.guard)
        page = self.client.get(self.url).content.decode()
        finder = page.index("Finder&#x27;s original report") if "Finder&#x27;s original report" in page else page.index("Finder's original report")
        self.assertIn(self.item.photo.url, page[finder:])  # public photo, its normal media URL
        self.assertIn("taken by the finder", page)
        private = reverse("security_private_file", args=["claim", self.claim.pk])
        self.assertIn(private, page)  # claimant photo only through the permission-checked view
        self.assertNotIn(self.claim.attachment.name, page)  # no storage path for private evidence

    def test_placeholders_when_photos_are_missing(self):
        item = self.found_item(title="Black umbrella")
        claim = claims.submit_claim(item, self.other, "Black umbrella, wooden handle", where_when="Canteen")
        self.client.force_login(self.guard)
        r = self.client.get(reverse("security_claim", args=[claim.pk]))
        self.assertContains(r, "No photo provided by the finder")
        self.assertContains(r, "No supporting photo provided")

    def test_only_authorised_staff_can_open_the_review_or_the_private_photo(self):
        private = reverse("security_private_file", args=["claim", self.claim.pk])
        for user in (self.student, self.other, self.finder):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403, user.username)
            self.assertEqual(self.client.get(private).status_code, 403, user.username)
        self.client.logout()
        self.assertEqual(self.client.get(private).status_code, 302)  # to sign-in
        self.client.force_login(self.guard)
        r = self.client.get(private)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_review_workflow_controls_are_unchanged(self):
        self.client.force_login(self.guard)
        r = self.client.get(self.url)
        for text in ("In-person verification", "confirm_compared", "Decision reason", "Request more information"):
            self.assertContains(r, text)
