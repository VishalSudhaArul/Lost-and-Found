from django.core.exceptions import PermissionDenied
from django.urls import reverse

from lostfound.models import Claim, PrivateDetail, VerificationChallenge
from lostfound.services import claims, verification
from lostfound.services.errors import WorkflowError

from .base import SECRET_ANSWER, SECRET_SERIAL, LFTestCase


class ScoringTests(LFTestCase):
    def test_exact_ignores_case_spaces_and_punctuation(self):
        self.assertEqual(verification.score_answer("exact", "SN-77QX-41", "sn 77qx41"), 1.0)
        self.assertEqual(verification.score_answer("exact", "SN-77QX-41", "SN-77QX-42"), 0.0)

    def test_keywords_tolerate_order_and_small_typos(self):
        ratio = verification.score_answer("keywords", "zebra sticker purple", "a purple zebra stiker")
        self.assertTrue(verification.answer_passes("keywords", ratio))
        self.assertFalse(verification.answer_passes("keywords", verification.score_answer("keywords", "zebra sticker purple", "red cat")))

    def test_blank_answer_scores_zero(self):
        self.assertEqual(verification.score_answer("fuzzy", "anything", "  "), 0.0)


class VerificationFlowTests(LFTestCase):
    def setUp(self):
        super().setUp()
        self.item = self.found_item()
        self.claim = claims.submit_claim(self.item, self.student, "It is my backpack, lost in the library yesterday.", with_challenge=True)

    def test_challenge_page_shows_questions_but_never_answers(self):
        self.client.force_login(self.student)
        r = self.client.get(reverse("claim_verify", args=[self.claim.pk]))
        self.assertContains(r, "What sticker is on the front?")
        for secret in (SECRET_ANSWER, "zebra", SECRET_SERIAL, "77QX"):
            self.assertNotContains(r, secret)
        self.assertEqual(r["Cache-Control"].count("no-cache") + r["Cache-Control"].count("no-store") > 0, True)

    def test_passing_moves_to_review_not_approval(self):
        result = verification.submit_attempt(self.claim.challenge.pk, self.student, self.correct_answers(self.claim))
        self.assertTrue(result["passed"])
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, Claim.Status.UNDER_REVIEW)
        self.assertEqual(self.claim.verification_status, Claim.Verification.PASSED)

    def test_failure_does_not_reveal_which_answer_was_wrong(self):
        answers = self.correct_answers(self.claim)
        first = next(iter(answers))
        answers[first] = "wrong"
        self.client.force_login(self.student)
        r = self.client.post(reverse("claim_verify", args=[self.claim.pk]), answers, follow=True)
        self.assertContains(r, "did not match")
        self.assertContains(r, "attempt(s) remaining")
        for word in ("sticker", "serial", "first", "second"):
            self.assertNotIn(word, str([str(m) for m in r.context["messages"]]).lower())

    def test_attempts_are_limited_and_lock(self):
        max_attempts = self.claim.challenge.max_attempts
        for i in range(max_attempts):
            result = verification.submit_attempt(self.claim.challenge.pk, self.student, {})
        self.assertTrue(result["locked"])
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.challenge.status, VerificationChallenge.Status.LOCKED)
        self.assertEqual(self.claim.verification_status, Claim.Verification.FAILED)
        with self.assertRaises(WorkflowError):
            verification.submit_attempt(self.claim.challenge.pk, self.student, self.correct_answers(self.claim))

    def test_failed_claim_cannot_be_approved(self):
        for _ in range(self.claim.challenge.max_attempts):
            verification.submit_attempt(self.claim.challenge.pk, self.student, {})
        with self.assertRaises(WorkflowError):
            claims.approve_claim(self.claim.pk, self.guard)

    def test_rate_limit_applies_across_claims(self):
        with self.settings(VERIFY_RATE_LIMIT=(1, 3600)):
            verification.submit_attempt(self.claim.challenge.pk, self.student, {})
            with self.assertRaisesMessage(WorkflowError, "Too many"):
                verification.submit_attempt(self.claim.challenge.pk, self.student, {})

    def test_other_user_cannot_answer_someone_elses_challenge(self):
        with self.assertRaises(PermissionDenied):
            verification.submit_attempt(self.claim.challenge.pk, self.other, {})

    def test_answers_are_stored_encrypted_and_viewing_is_audited(self):
        from lostfound.models import AuditEntry
        verification.submit_attempt(self.claim.challenge.pk, self.student, self.correct_answers(self.claim))
        attempt = self.claim.challenge.attempts.get()
        self.assertNotIn("zebra", attempt.responses_ciphertext)
        data = verification.review_data(self.claim, self.guard)
        self.assertEqual(data["attempts"][0]["rows"][0]["response"], data["attempts"][0]["rows"][0]["expected"])
        self.assertTrue(AuditEntry.objects.filter(action="private_detail.viewed", actor=self.guard).exists())
        with self.assertRaises(PermissionDenied):
            verification.review_data(self.claim, self.student)

    def test_claim_without_private_details_waits_then_gets_challenge(self):
        item = self.found_item(title="Umbrella", details=False)
        claim = claims.submit_claim(item, self.other, "That is my black umbrella from the library.", with_challenge=True)
        self.assertFalse(VerificationChallenge.objects.filter(claim=claim).exists())
        verification.add_private_detail(item, self.guard, "mark", "What colour is the handle?", "wooden brown")
        issued = verification.issue_pending_challenges(item)
        self.assertEqual(len(issued), 1)
        self.assertEqual(PrivateDetail.vault.for_item(item).count(), 1)
