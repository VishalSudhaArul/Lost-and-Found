"""Shared fixtures. Every test runs with a throwaway encryption key and
temporary media directories so no real secrets or files are touched."""
import io
import shutil
import tempfile
from datetime import timedelta

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from lostfound import roles
from lostfound.models import Item, ItemCategory, Location, OperationalSettings, CategoryAttribute
from lostfound.services import claims, custody, verification

User = get_user_model()
TEST_KEY = Fernet.generate_key().decode()
TMP_MEDIA = tempfile.mkdtemp(prefix="lf-media-")
TMP_PRIVATE = tempfile.mkdtemp(prefix="lf-private-")

SECRET_ANSWER = "zebra sticker purple"
SECRET_SERIAL = "SN-77QX-41"


def jpeg_bytes(size=(64, 48), exif_gps=False):
    img = Image.new("RGB", size, (200, 30, 30))
    buf = io.BytesIO()
    if exif_gps:
        exif = Image.Exif()
        exif[0x010F] = "TestCam"          # Make
        exif[0x8825] = {1: "N", 2: (12.0, 30.0, 0.0)}  # GPSInfo
        img.save(buf, format="JPEG", exif=exif)
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


def upload(name="photo.jpg", data=None, content_type="image/jpeg"):
    return SimpleUploadedFile(name, data if data is not None else jpeg_bytes(), content_type=content_type)


@override_settings(FIELD_ENCRYPTION_KEYS=[TEST_KEY], MEDIA_ROOT=TMP_MEDIA, PRIVATE_MEDIA_ROOT=TMP_PRIVATE,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], ALLOWED_HOSTS=["testserver"])
class LFTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        shutil.rmtree(TMP_PRIVATE, ignore_errors=True)

    def setUp(self):
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.cfg = OperationalSettings.load()
        cls.bags = ItemCategory.objects.create(name="Bags")
        cls.backpacks = ItemCategory.objects.create(name="Backpacks", parent=cls.bags)
        CategoryAttribute.objects.create(category=cls.bags, key="colour", label="Colour")
        cls.electronics = ItemCategory.objects.create(name="Electronics")
        cls.phones = ItemCategory.objects.create(name="Phones", parent=cls.electronics)
        cls.campus = Location.objects.create(name="Campus", kind="area")
        cls.library = Location.objects.create(name="Library", parent=cls.campus)
        cls.hall = Location.objects.create(name="Reading hall", parent=cls.library, kind="floor")
        cls.canteen = Location.objects.create(name="Canteen", parent=cls.campus)
        cls.office = Location.objects.create(name="Security Office", parent=cls.campus, is_custody_point=True)
        cls.student = cls.make_user("student", roles.MEMBERS)
        cls.other = cls.make_user("other", roles.MEMBERS)
        cls.third = cls.make_user("third", roles.MEMBERS)
        cls.finder = cls.make_user("finder", roles.MEMBERS)
        cls.guard = cls.make_user("guard", roles.SECURITY)
        cls.guard2 = cls.make_user("guard2", roles.SECURITY)
        cls.cadmin = cls.make_user("cadmin", roles.CAMPUS_ADMIN)
        cls.sysadmin = cls.make_user("sysadmin", roles.SYSTEM_ADMIN)

    @classmethod
    def make_user(cls, username, group):
        u = User.objects.create_user(username, f"{username}@uni.test", "pw-Long-enough-1", first_name=username.title())
        u.groups.add(Group.objects.get(name=group))
        return u

    def found_item(self, title="Black backpack", reporter=None, receive=True, details=True, **kw):
        defaults = dict(report_type=Item.ReportType.FOUND, title=title, description="Black backpack found on a bench.",
                        category=self.backpacks, location=self.hall, occurred_at=timezone.now() - timedelta(hours=2),
                        reporter=reporter or self.finder, attributes={"colour": "black"})
        defaults.update(kw)
        item = Item.objects.create(**defaults)
        if details:
            verification.add_private_detail(item, item.reporter, "mark", "What sticker is on the front?", SECRET_ANSWER)
            verification.add_private_detail(item, item.reporter, "identifier", "What is the serial number?", SECRET_SERIAL, "exact")
        if receive:
            custody.receive_item(item.pk, self.guard, self.office)
            item.refresh_from_db()
        return item

    def lost_item(self, title="Lost black backpack", reporter=None, **kw):
        defaults = dict(report_type=Item.ReportType.LOST, title=title, description="Lost my black backpack in the library.",
                        category=self.backpacks, location=self.hall, occurred_at=timezone.now() - timedelta(hours=3),
                        reporter=reporter or self.student, attributes={"colour": "black"})
        defaults.update(kw)
        return Item.objects.create(**defaults)

    def correct_answers(self, claim):
        from lostfound.models import PrivateDetail
        answers = {}
        for pk in claim.challenge.detail_ids:
            answers[str(pk)] = PrivateDetail.vault.get(pk=pk).decrypt_answer()
        return answers

    def verified_claim(self, item=None, claimant=None):
        item = item or self.found_item()
        claimant = claimant or self.student
        claim = claims.submit_claim(item, claimant, "It is my backpack, I lost it yesterday in the library.", with_challenge=True)
        verification.submit_attempt(claim.challenge.pk, claimant, self.correct_answers(claim))
        claim.refresh_from_db()
        return claim

    def approved_claim(self, **kw):
        claim = self.verified_claim(**kw)
        claims.approve_claim(claim.pk, self.guard)
        claim.refresh_from_db()
        return claim

    def scheduled_handover(self, method="otp", **kw):
        claim = self.approved_claim(**kw)
        return custody.schedule_handover(claim.pk, self.guard, timezone.now() + timedelta(hours=1), self.office, self.guard, method)
