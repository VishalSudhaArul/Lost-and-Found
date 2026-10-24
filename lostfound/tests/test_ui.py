"""Regression tests for the redesigned UI: every screen renders for every
role, role-based access holds, private details stay out of every non-review
screen, and the new board filters and password reset really work."""
import re
from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from lostfound.models import Item, MatchSuggestion

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase

PRIVATE = (SECRET_ANSWER, SECRET_SERIAL, "77QX", "zebra")


class UIFixture(LFTestCase):
    def setUp(self):
        super().setUp()
        self.handover = self.scheduled_handover()
        self.claim = self.handover.claim
        self.item = self.claim.item
        self.lost = self.lost_item()
        MatchSuggestion.objects.update_or_create(lost_item=self.lost, found_item=self.found_item(title="Blue bottle"),
                                                 defaults={"score": 0.8, "status": MatchSuggestion.Status.SUGGESTED})

    def get(self, user, name, *args, query=""):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(reverse(name, args=args) + query)


class PagesRenderTests(UIFixture):
    def test_public_pages_render_for_visitors_and_members(self):
        for user in (None, self.student):
            for name, args in (("home", ()), ("board", ()), ("login", ()), ("register", ()), ("password_reset", ()),
                               ("password_reset_done", ())):
                r = self.get(user, name, *args)
                self.assertIn(r.status_code, (200, 302), f"{name} as {user}")
        r = self.get(None, "home")
        self.assertContains(r, 'class="site-header"')          # public shell for visitors
        r = self.get(self.student, "home")
        self.assertContains(r, 'class="sidebar"')              # app shell when signed in

    def test_member_pages_render(self):
        pages = [("dashboard", ()), ("my_reports", ()), ("my_claims", ()), ("notifications", ()),
                 ("report_item", ("lost",)), ("report_item", ("found",)), ("claim_detail", (self.claim.pk,)),
                 ("report_detail", (self.lost.pk,)), ("report_matches", (self.lost.pk,)), ("password_change", ())]
        for name, args in pages:
            self.assertEqual(self.get(self.student, name, *args).status_code, 200, name)

    def test_security_pages_render(self):
        pages = [("security_dashboard", ()), ("security_items", ()), ("security_item", (self.item.pk,)),
                 ("security_claims", ()), ("security_claim", (self.claim.pk,)), ("security_handovers", ()),
                 ("security_handover", (self.handover.pk,)), ("security_custody", ()), ("security_ageing", ())]
        for name, args in pages:
            self.assertEqual(self.get(self.guard, name, *args).status_code, 200, name)

    def test_admin_pages_render(self):
        for user, pages in ((self.cadmin, ["admin_overview", "admin_analytics", "admin_categories", "admin_locations",
                                            "admin_settings", "admin_audit"]),
                            (self.sysadmin, ["admin_overview", "admin_users", "admin_settings"])):
            for name in pages:
                self.assertEqual(self.get(user, name).status_code, 200, f"{name} as {user}")

    def test_error_pages_use_branded_template(self):
        r = self.client.get("/definitely-not-a-page/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "Page not found", status_code=404)
        r = self.get(self.student, "security_dashboard")
        self.assertContains(r, "have access to this page", status_code=403)


class RoleAccessTests(UIFixture):
    def test_members_cannot_open_staff_screens(self):
        for name in ("security_dashboard", "security_items", "security_claims", "security_handovers",
                     "security_custody", "security_ageing", "admin_overview", "admin_users", "admin_analytics",
                     "admin_categories", "admin_audit"):
            self.assertEqual(self.get(self.student, name).status_code, 403, name)
        self.assertEqual(self.get(self.student, "security_claim", self.claim.pk).status_code, 403)

    def test_visitors_are_sent_to_sign_in(self):
        for name, args in (("dashboard", ()), ("report_item", ("lost",)), ("security_dashboard", ()), ("admin_users", ())):
            r = self.get(None, name, *args)
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse("login"), r["Location"])

    def test_roles_only_see_their_own_tools(self):
        self.assertEqual(self.get(self.guard, "admin_users").status_code, 403)
        self.assertEqual(self.get(self.cadmin, "admin_users").status_code, 403)
        self.assertEqual(self.get(self.sysadmin, "security_claims").status_code, 403)
        # Navigation mirrors permissions: members don't see staff links.
        r = self.get(self.student, "dashboard")
        self.assertNotContains(r, reverse("security_claims"))
        self.assertNotContains(r, reverse("admin_users"))

    def test_other_users_claims_and_reports_are_not_found(self):
        self.assertEqual(self.get(self.other, "claim_detail", self.claim.pk).status_code, 404)
        self.assertEqual(self.get(self.other, "report_detail", self.lost.pk).status_code, 404)


class PrivateDetailIsolationTests(UIFixture):
    def assert_clean(self, response, where):
        text = response.content.decode().lower()
        for secret in PRIVATE:
            self.assertNotIn(secret.lower(), text, f"private detail leaked on {where}")

    def test_claimant_and_finder_screens_never_include_answers(self):
        for user, name, args in ((self.student, "claim_detail", (self.claim.pk,)), (self.student, "dashboard", ()),
                                 (self.student, "my_claims", ()), (self.student, "notifications", ()),
                                 (self.student, "item_detail", (self.item.pk,)),
                                 (self.finder, "report_detail", (self.item.pk,)), (self.finder, "dashboard", ())):
            self.assert_clean(self.get(user, name, *args), f"{name} as {user}")

    def test_verification_page_shows_questions_not_answers(self):
        claim = __import__("lostfound.services.claims", fromlist=["x"]).submit_claim(
            self.found_item(title="Grey backpack"), self.other, "This grey backpack is mine, lost it on Monday.")
        r = self.get(self.other, "claim_verify", claim.pk)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "What sticker is on the front?")
        self.assert_clean(r, "claim_verify")
        self.assertNotIn("localStorage", r.content.decode())

    def test_security_item_screen_hides_answers_until_claim_review(self):
        self.assert_clean(self.get(self.guard, "security_item", self.item.pk), "security_item")
        self.assert_clean(self.get(self.guard, "security_dashboard"), "security_dashboard")
        # The review screen is the one place authorised staff see the evidence.
        self.assertContains(self.get(self.guard, "security_claim", self.claim.pk), SECRET_ANSWER)

    def test_report_form_does_not_embed_private_values_in_page_data(self):
        r = self.get(self.finder, "report_item", "found")
        scripts = " ".join(re.findall(r"<script[^>]*>(.*?)</script>", r.content.decode(), re.S)).lower()
        for secret in PRIVATE:
            self.assertNotIn(secret.lower(), scripts)


class BoardAndHomeTests(UIFixture):
    def test_to_date_filter_excludes_later_items(self):
        old = self.found_item(title="Old umbrella", occurred_at=timezone.now() - timedelta(days=20), details=False)
        cutoff = (timezone.localdate() - timedelta(days=10)).isoformat()
        r = self.client.get(reverse("board"), {"to": cutoff})
        ids = [i.id for i in r.context["items"]]
        self.assertEqual(ids, [old.pk])

    def test_list_view_toggle_is_server_rendered(self):
        self.assertContains(self.client.get(reverse("board"), {"view": "list"}), 'class="tags as-list"')
        self.assertNotContains(self.client.get(reverse("board")), 'class="tags as-list"')

    def test_home_counts_come_from_the_database_and_label_sample_data(self):
        r = self.client.get(reverse("home"))
        self.assertEqual(r.context["stats"]["open_found"], Item.objects.found().publicly_listed().count())
        self.assertNotContains(r, "clearly labelled sample data")
        self.found_item(title="[Sample] Red umbrella", details=False)
        self.assertContains(self.client.get(reverse("home")), "clearly labelled sample data")

    def test_member_dashboard_lists_real_matches(self):
        r = self.get(self.student, "dashboard")
        self.assertEqual(len(r.context["top_matches"]), 1)
        self.assertContains(r, "80% match")


class PasswordResetTests(LFTestCase):
    def test_reset_by_email_end_to_end(self):
        r = self.client.post(reverse("password_reset"), {"email": "student@uni.test"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"https?://[^/]+(/accounts/password/reset/\S+/)", mail.outbox[0].body).group(1)
        r = self.client.get(link, follow=True)
        self.assertContains(r, "Choose a new password")
        r = self.client.post(r.redirect_chain[-1][0], {"new_password1": "Fresh-pass-2026!", "new_password2": "Fresh-pass-2026!"})
        self.assertRedirects(r, reverse("password_reset_complete"))
        self.assertTrue(self.client.login(username="student", password="Fresh-pass-2026!"))

    def test_unknown_email_reveals_nothing(self):
        r = self.client.post(reverse("password_reset"), {"email": "nobody@uni.test"})
        self.assertRedirects(r, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)
