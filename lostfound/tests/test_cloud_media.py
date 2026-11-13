"""Cloudinary media storage (Render free tier). Cloudinary's API is mocked; no network calls."""
from io import StringIO
from unittest import mock

from django.core.files.base import ContentFile
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.urls import reverse

from lostfound.models import Claim
from lostfound.services import claims, public
from lostfound.services.cloud_storage import PrivateCloudinaryStorage, PublicCloudinaryStorage
from lostfound.services.storage import private_storage_factory

from .base import LFTestCase, jpeg_bytes, upload

CLOUD = "cloudinary://key123:secret456@democloud"
CLOUD_SETTINGS = dict(
    CLOUDINARY_URL=CLOUD,
    STORAGES={"default": {"BACKEND": "lostfound.services.cloud_storage.PublicCloudinaryStorage"},
              "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})


class _Resp:
    def __init__(self, data):
        self._d = data

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@override_settings(**CLOUD_SETTINGS)
class CloudStorageTests(LFTestCase):
    def test_public_photo_is_uploaded_with_same_name_and_served_from_cdn(self):
        storage = PublicCloudinaryStorage()
        with mock.patch("cloudinary.uploader.upload") as up:
            name = storage.save("items/2026/10/abc123.jpg", ContentFile(b"img", name="abc123.jpg"))
        self.assertEqual(name, "items/2026/10/abc123.jpg")
        kwargs = up.call_args.kwargs
        self.assertEqual((kwargs["public_id"], kwargs["type"], kwargs["overwrite"]), ("items/2026/10/abc123", "upload", False))
        url = storage.url(name)
        self.assertTrue(url.startswith("https://res.cloudinary.com/democloud/image/upload/"))
        self.assertTrue(url.endswith("items/2026/10/abc123.jpg"))

    def test_private_evidence_is_authenticated_and_never_gets_a_url(self):
        storage = private_storage_factory()
        with mock.patch("cloudinary.uploader.upload") as up:
            storage.save("claims/2026/10/secret.jpg", ContentFile(b"evidence", name="secret.jpg"))
        self.assertEqual(up.call_args.kwargs["type"], "authenticated")
        with self.assertRaises(ValueError):
            storage.url("claims/2026/10/secret.jpg")
        with self.assertRaises(ValueError):
            PrivateCloudinaryStorage().url("claims/2026/10/secret.jpg")

    def test_report_with_photo_goes_to_cloud_and_board_links_to_it(self):
        with mock.patch("cloudinary.uploader.upload") as up:
            item = self.found_item(title="Blue bottle", photo=None)
            item.photo.save("x.jpg", ContentFile(jpeg_bytes(), name="x.jpg"))
        up.assert_called_once()
        card = public.public_item(item.pk)
        self.assertRegex(card.photo_url, r"^https://res\.cloudinary\.com/democloud/image/upload/(v\d+/)?items/.+\.jpg$")

    def test_private_file_view_fetches_server_side_with_permission_check(self):
        item = self.found_item()
        with mock.patch("cloudinary.uploader.upload"):
            claim = claims.submit_claim(item, self.student, "My black backpack", where_when="Library",
                                        attachment=__import__("lostfound.services.images", fromlist=["x"]).process_upload(upload()))
        url = reverse("security_private_file", args=["claim", claim.pk])
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.guard)
        with mock.patch("urllib.request.urlopen", return_value=_Resp(b"jpegbytes")) as urlopen:
            r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b"".join(r.streaming_content), b"jpegbytes")
        fetched = urlopen.call_args[0][0]
        self.assertIn("/image/authenticated/s--", fetched)  # signed URL used only on the server
        self.assertNotIn("cloudinary", str(r.headers).lower())
        self.assertEqual(r["Cache-Control"], "private, no-store")


class UploadCommandTests(LFTestCase):
    def test_refuses_without_cloudinary_url(self):
        with override_settings(CLOUDINARY_URL="", CLOUDINARY_MIGRATION_URL=""), self.assertRaises(CommandError):
            call_command("upload_media_to_cloud", stdout=StringIO())

    @override_settings(CLOUDINARY_URL=CLOUD)
    def test_dry_run_then_apply_uploads_verifies_and_keeps_local_files(self):
        item = self.found_item(title="With photo")
        item.photo.save("p.jpg", ContentFile(jpeg_bytes(), name="p.jpg"))  # local default storage here
        local = item.photo.path
        data = open(local, "rb").read()
        out = StringIO()
        with mock.patch("cloudinary.api.ping"):
            call_command("upload_media_to_cloud", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn(item.photo.name, out.getvalue())
        with mock.patch("cloudinary.api.ping"), \
                mock.patch("cloudinary.api.resource", side_effect=__import__("cloudinary").exceptions.NotFound("x")), \
                mock.patch("cloudinary.uploader.upload") as up, \
                mock.patch("urllib.request.urlopen", return_value=_Resp(data)):
            out = StringIO()
            call_command("upload_media_to_cloud", "--apply", "--backup-dir", str(self.tmp_backup()), stdout=out)
        up.assert_called_once()
        self.assertIn("Uploaded 1, already present 0, failed 0", out.getvalue())
        self.assertEqual(open(local, "rb").read(), data)  # local file untouched

    def tmp_backup(self):
        import tempfile
        return tempfile.mkdtemp(prefix="lf-mediabackup-")


class MigrationSafetyTests(LFTestCase):
    @override_settings(CLOUDINARY_URL="", CLOUDINARY_MIGRATION_URL=CLOUD)
    def test_migration_key_does_not_switch_the_local_site_to_cloudinary(self):
        from lostfound.services.cloud_storage import cloudinary_enabled
        from lostfound.services.storage import PrivateStorage
        self.assertFalse(cloudinary_enabled())
        self.assertIsInstance(private_storage_factory()._target(), PrivateStorage)

    @override_settings(CLOUDINARY_URL="", CLOUDINARY_MIGRATION_URL=CLOUD)
    def test_failed_authentication_stops_before_any_upload(self):
        import cloudinary
        item = self.found_item(title="With photo")
        item.photo.save("p.jpg", ContentFile(jpeg_bytes(), name="p.jpg"))
        with mock.patch("cloudinary.api.ping", side_effect=cloudinary.exceptions.AuthorizationRequired("no")), \
                mock.patch("cloudinary.uploader.upload") as up, self.assertRaises(CommandError):
            call_command("upload_media_to_cloud", "--apply", stdout=StringIO())
        up.assert_not_called()

    @override_settings(CLOUDINARY_URL="", CLOUDINARY_MIGRATION_URL=CLOUD)
    def test_check_only_pings(self):
        out = StringIO()
        with mock.patch("cloudinary.api.ping") as ping, mock.patch("cloudinary.uploader.upload") as up:
            call_command("upload_media_to_cloud", "--check", stdout=out)
        ping.assert_called_once()
        up.assert_not_called()
        self.assertIn("authentication succeeded", out.getvalue())
        self.assertNotIn("secret456", out.getvalue())

    @override_settings(CLOUDINARY_URL="", CLOUDINARY_MIGRATION_URL=CLOUD)
    def test_backup_is_verified_against_local_files(self):
        import tempfile
        import zipfile
        from pathlib import Path
        item = self.found_item(title="With photo")
        item.photo.save("p.jpg", ContentFile(jpeg_bytes(), name="p.jpg"))
        with open(item.photo.path, "rb") as fh:
            original = fh.read()
        good = Path(tempfile.mkdtemp()) / "good.zip"
        with zipfile.ZipFile(good, "w") as z:
            z.writestr("media/" + item.photo.name, original)
        bad = Path(tempfile.mkdtemp()) / "bad.zip"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("media/" + item.photo.name, b"different")
        with mock.patch("cloudinary.api.ping"):
            out = StringIO()
            call_command("upload_media_to_cloud", "--verify-backup", str(good), stdout=out)
            self.assertIn("Backup verified", out.getvalue())
            with self.assertRaises(CommandError):
                call_command("upload_media_to_cloud", "--verify-backup", str(bad), stdout=StringIO())


@override_settings(**CLOUD_SETTINGS)
class CloudFailureTests(LFTestCase):
    def payload(self, **kw):
        from datetime import timedelta
        from django.utils import timezone
        data = {"title": "Blue water bottle", "category": self.backpacks.pk, "description": "Blue bottle with a dent.",
                "occurred_at": (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                "location": self.hall.pk, "location_note": "", "attr_colour": "blue"}
        data.update(kw)
        return data

    def test_upload_failure_saves_nothing_and_keeps_the_form(self):
        import cloudinary
        from lostfound.models import Item
        self.client.force_login(self.student)
        before = Item.objects.count()
        with mock.patch("cloudinary.uploader.upload", side_effect=cloudinary.exceptions.Error("network down")):
            r = self.client.post(reverse("report_item", args=["lost"]), {**self.payload(), "photo": upload()})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "photo couldn")
        self.assertContains(r, "Blue water bottle")  # input kept
        self.assertEqual(Item.objects.count(), before)  # no half-saved report / broken reference

    def test_successful_cloud_upload_shows_on_detail_and_board(self):
        from lostfound.models import Item
        self.client.force_login(self.student)
        with mock.patch("cloudinary.uploader.upload") as up:
            r = self.client.post(reverse("report_item", args=["lost"]), {**self.payload(), "photo": upload()})
        up.assert_called_once()
        item = Item.objects.latest("pk")
        self.assertRedirects(r, reverse("report_detail", args=[item.pk]))
        self.assertTrue(item.photo.name.startswith("items/"))
        detail = self.client.get(reverse("report_detail", args=[item.pk]))
        self.assertContains(detail, "res.cloudinary.com/democloud/image/upload/")

    def test_invalid_file_type_is_rejected_before_any_upload(self):
        self.client.force_login(self.student)
        with mock.patch("cloudinary.uploader.upload") as up:
            r = self.client.post(reverse("report_item", args=["lost"]),
                                 {**self.payload(), "photo": upload("notes.txt", b"not an image", "text/plain")})
        up.assert_not_called()
        self.assertEqual(r.status_code, 200)

    def test_private_fetch_failure_gives_clean_503_not_a_crash(self):
        import urllib.error
        item = self.found_item()
        with mock.patch("cloudinary.uploader.upload"):
            claim = claims.submit_claim(item, self.student, "My black backpack", where_when="Library",
                                        attachment=__import__("lostfound.services.images", fromlist=["x"]).process_upload(upload()))
        self.client.force_login(self.guard)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            r = self.client.get(reverse("security_private_file", args=["claim", claim.pk]))
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(b"cloudinary", r.content.lower().replace(b"res.cloudinary", b""))
