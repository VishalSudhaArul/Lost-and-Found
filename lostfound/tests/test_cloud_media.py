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
        with override_settings(CLOUDINARY_URL=""), self.assertRaises(CommandError):
            call_command("upload_media_to_cloud", stdout=StringIO())

    @override_settings(CLOUDINARY_URL=CLOUD)
    def test_dry_run_then_apply_uploads_verifies_and_keeps_local_files(self):
        item = self.found_item(title="With photo")
        item.photo.save("p.jpg", ContentFile(jpeg_bytes(), name="p.jpg"))  # local default storage here
        local = item.photo.path
        data = open(local, "rb").read()
        out = StringIO()
        call_command("upload_media_to_cloud", stdout=out)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn(item.photo.name, out.getvalue())
        with mock.patch("cloudinary.api.resource", side_effect=__import__("cloudinary").exceptions.NotFound("x")), \
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
