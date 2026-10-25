"""Administrator block/unblock/delete of student and teacher accounts."""
from django.urls import reverse

from lostfound.models import AuditEntry, Item

from .base import LFTestCase, User

PW = "pw-Long-enough-1"


class BlockTests(LFTestCase):
    def test_block_signs_out_everywhere_and_unblock_restores(self):
        other_browser = self.client_class()
        other_browser.force_login(self.student)
        self.assertEqual(other_browser.get(reverse("dashboard")).status_code, 200)
        self.client.force_login(self.sysadmin)
        r = self.client.post(reverse("admin_user_block", args=[self.student.pk]), {"reason": "Repeated fake reports"})
        self.assertRedirects(r, reverse("admin_user_edit", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertEqual(other_browser.get(reverse("dashboard")).status_code, 302)  # signed out
        self.assertTrue(AuditEntry.objects.filter(action="users.blocked").exists())
        self.client.post(reverse("admin_user_block", args=[self.student.pk]), {"action": "unblock"})
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)

    def test_block_needs_reason_and_can_not_target_self_or_superuser(self):
        self.client.force_login(self.sysadmin)
        self.client.post(reverse("admin_user_block", args=[self.student.pk]), {"reason": ""})
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)
        self.client.post(reverse("admin_user_block", args=[self.sysadmin.pk]), {"reason": "Testing self block"})
        self.sysadmin.refresh_from_db()
        self.assertTrue(self.sysadmin.is_active)
        boss = User.objects.create_superuser("boss", "boss@uni.test", PW)
        self.client.post(reverse("admin_user_block", args=[boss.pk]), {"reason": "Trying to block the superuser"})
        boss.refresh_from_db()
        self.assertTrue(boss.is_active)

    def test_only_user_managers_can_block_or_delete(self):
        for actor in (self.student, self.guard, self.cadmin):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(reverse("admin_user_block", args=[self.other.pk]),
                                              {"reason": "not allowed here"}).status_code, 403)
            self.assertEqual(self.client.post(reverse("admin_user_delete", args=[self.other.pk]),
                                              {"confirm_username": "other", "confirm_password": PW}).status_code, 403)


class DeleteTests(LFTestCase):
    def test_account_without_history_is_removed(self):
        self.client.force_login(self.sysadmin)
        r = self.client.post(reverse("admin_user_delete", args=[self.third.pk]),
                             {"confirm_username": "third", "confirm_password": PW})
        self.assertRedirects(r, reverse("admin_users"))
        self.assertFalse(User.objects.filter(pk=self.third.pk).exists())

    def test_account_with_history_is_anonymised_and_records_kept(self):
        item = self.lost_item(reporter=self.student)
        self.client.force_login(self.sysadmin)
        self.client.post(reverse("admin_user_delete", args=[self.student.pk]),
                         {"confirm_username": "student", "confirm_password": PW})
        user = User.objects.get(pk=self.student.pk)
        self.assertEqual(user.username, f"deleted-user-{user.pk}")
        self.assertEqual(user.email, "")
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.groups.exists())
        self.assertTrue(Item.objects.filter(pk=item.pk, reporter=user).exists())
        self.assertFalse(self.client.login(username="student", password=PW))

    def test_delete_needs_exact_username_and_admin_password(self):
        self.client.force_login(self.sysadmin)
        self.client.post(reverse("admin_user_delete", args=[self.third.pk]),
                         {"confirm_username": "Third", "confirm_password": PW})
        self.client.post(reverse("admin_user_delete", args=[self.third.pk]),
                         {"confirm_username": "third", "confirm_password": "wrong"})
        self.assertTrue(User.objects.filter(pk=self.third.pk, username="third").exists())
        self.client.post(reverse("admin_user_delete", args=[self.sysadmin.pk]),
                         {"confirm_username": "sysadmin", "confirm_password": PW})
        self.assertTrue(User.objects.filter(pk=self.sysadmin.pk, username="sysadmin").exists())

    def test_account_page_shows_danger_zone_only_for_others(self):
        self.client.force_login(self.sysadmin)
        self.assertContains(self.client.get(reverse("admin_user_edit", args=[self.student.pk])), "Danger zone")
        self.assertNotContains(self.client.get(reverse("admin_user_edit", args=[self.sysadmin.pk])), "Danger zone")
