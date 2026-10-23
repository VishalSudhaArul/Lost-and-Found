"""M4 — ownership verification challenges drawn from PrivateDetail.

Scoring rules (explicit, chosen per detail by whoever records it):
* EXACT    — answers equal after removing case, spaces and punctuation
             (so "SN 12-ab" == "sn12ab").
* KEYWORDS — at least 60% of the answer's key words (stop-words removed) appear
             in the response; small typos in words of 5+ letters are tolerated.
* FUZZY    — character similarity of the normalised strings is at least 0.8.
Each detail earns its weight when it passes. The challenge passes when the
earned share reaches ``verification_pass_threshold``. Passing never approves a
claim: it moves the claim to security review.
"""
import difflib
import re
import secrets

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from ..models import Claim, OperationalSettings, PrivateDetail, VerificationAttempt, VerificationChallenge
from . import audit, crypto, notifications, ratelimit
from .errors import WorkflowError

STOP_WORDS = {"a", "an", "the", "and", "or", "of", "on", "in", "at", "to", "with", "is", "it", "its", "my", "there",
              "inside", "has", "have", "some", "this", "that", "for", "from", "by", "be", "are", "was"}
KEYWORD_PASS_RATIO = 0.6
FUZZY_PASS_RATIO = 0.8
WORD_RE = re.compile(r"[a-z0-9]+")


def normalise_compact(text):
    return "".join(WORD_RE.findall((text or "").lower()))


def normalise_words(text):
    return WORD_RE.findall((text or "").lower())


def _word_present(word, response_words):
    if word in response_words:
        return True
    if len(word) >= 5:
        return any(difflib.SequenceMatcher(None, word, w).ratio() >= 0.8
                   for w in response_words if abs(len(w) - len(word)) <= 2)
    return False


def score_answer(mode, expected, response):
    """Return a value in [0, 1] describing how well ``response`` matches."""
    if not (response or "").strip():
        return 0.0
    if mode == PrivateDetail.MatchMode.EXACT:
        return 1.0 if normalise_compact(expected) == normalise_compact(response) else 0.0
    if mode == PrivateDetail.MatchMode.FUZZY:
        return difflib.SequenceMatcher(None, " ".join(normalise_words(expected)),
                                       " ".join(normalise_words(response))).ratio()
    keys = [w for w in normalise_words(expected) if w not in STOP_WORDS] or normalise_words(expected)
    if not keys:
        return 0.0
    response_words = set(normalise_words(response))
    return sum(1 for w in keys if _word_present(w, response_words)) / len(keys)


def answer_passes(mode, ratio):
    if mode == PrivateDetail.MatchMode.EXACT:
        return ratio >= 1.0
    if mode == PrivateDetail.MatchMode.FUZZY:
        return ratio >= FUZZY_PASS_RATIO
    return ratio >= KEYWORD_PASS_RATIO


def add_private_detail(item, recorded_by, kind, question, answer, match_mode=PrivateDetail.MatchMode.KEYWORDS, weight=1):
    detail = PrivateDetail(item=item, kind=kind, match_mode=match_mode, weight=weight, recorded_by=recorded_by)
    detail.set_question(question.strip())
    detail.set_answer(answer.strip())
    detail.save()
    audit.record(recorded_by, "private_detail.recorded", f"Private detail recorded for {item.reference}", target=item,
                 detail_kind=kind)
    return detail


def issue_challenge(claim, cfg=None):
    cfg = cfg or OperationalSettings.load()
    existing = VerificationChallenge.objects.filter(claim=claim).first()
    if existing:
        return existing
    details = list(PrivateDetail.vault.for_item(claim.item).values_list("id", flat=True))
    if not details:
        return None
    chosen = secrets.SystemRandom().sample(details, min(cfg.challenge_question_count, len(details)))
    return VerificationChallenge.objects.create(
        claim=claim, detail_ids=chosen, max_attempts=cfg.verification_max_attempts,
        pass_threshold=cfg.verification_pass_threshold)


def issue_pending_challenges(item):
    """After security adds private details, give waiting claims a challenge."""
    issued = []
    for claim in Claim.objects.filter(item=item, status=Claim.Status.PENDING_VERIFICATION, challenge__isnull=True):
        challenge = issue_challenge(claim)
        if challenge:
            issued.append(challenge)
            notifications.notify([claim.claimant], "verification", "Verification questions ready",
                                 f"Answer the verification questions for claim {claim.reference}.",
                                 link=f"/my/claims/{claim.pk}/verify/")
    return issued


def questions_for_claimant(challenge):
    """Questions only — never answers."""
    details = {d.pk: d for d in PrivateDetail.vault.filter(pk__in=challenge.detail_ids)}
    return [{"id": pk, "kind": details[pk].get_kind_display(), "question": details[pk].decrypt_question()}
            for pk in challenge.detail_ids if pk in details]


def submit_attempt(challenge_id, user, responses, request=None):
    """Score answers. Returns a dict safe to show the claimant: it reveals
    only pass/fail and remaining attempts, never which answer was wrong."""
    limit, window = settings.VERIFY_RATE_LIMIT
    try:
        ratelimit.hit("verify", user.pk, limit, window)
    except ratelimit.RateLimited:
        raise WorkflowError("Too many verification attempts. Please wait and try again later.")

    with transaction.atomic():
        challenge = VerificationChallenge.objects.select_for_update().get(pk=challenge_id)
        claim = Claim.objects.select_related("item").get(pk=challenge.claim_id)
        if claim.claimant_id != user.pk:
            raise PermissionDenied("This is not your claim.")
        if claim.status != Claim.Status.PENDING_VERIFICATION or challenge.status != VerificationChallenge.Status.OPEN:
            raise WorkflowError("This verification challenge is closed.")
        if challenge.attempts_used >= challenge.max_attempts:
            raise WorkflowError("No verification attempts remain.")

        details = {d.pk: d for d in PrivateDetail.vault.filter(pk__in=challenge.detail_ids)}
        earned = total = 0
        per_question, clean_responses = {}, {}
        for pk in challenge.detail_ids:
            detail = details.get(pk)
            if detail is None:
                continue
            response = str(responses.get(str(pk)) or "")[:500]
            clean_responses[str(pk)] = response
            ratio = score_answer(detail.match_mode, detail.decrypt_answer(), response)
            ok = answer_passes(detail.match_mode, ratio)
            per_question[str(pk)] = {"similarity": round(ratio, 3), "passed": ok, "weight": detail.weight}
            total += detail.weight
            earned += detail.weight if ok else 0
        score = round(earned / total, 4) if total else 0.0
        passed = total > 0 and score >= challenge.pass_threshold

        VerificationAttempt.objects.create(
            challenge=challenge, responses_ciphertext=crypto.encrypt_json(clean_responses),
            question_scores=per_question, score=score, passed=passed, ip_address=audit.client_ip(request))
        challenge.attempts_used += 1
        challenge.best_score = max(challenge.best_score, score)

        reviewers = list(notifications.users_with_perm("review_claims"))
        if passed:
            challenge.status = VerificationChallenge.Status.PASSED
            challenge.completed_at = timezone.now()
            claim.status = Claim.Status.UNDER_REVIEW
            claim.verification_status = Claim.Verification.PASSED
            notifications.notify(reviewers, "verification", "Claim ready for review",
                                 f"Claim {claim.reference} on {claim.item.reference} passed verification.",
                                 link=f"/security/claims/{claim.pk}/")
        elif challenge.attempts_used >= challenge.max_attempts:
            challenge.status = VerificationChallenge.Status.LOCKED
            challenge.completed_at = timezone.now()
            claim.status = Claim.Status.UNDER_REVIEW
            claim.verification_status = Claim.Verification.FAILED
            notifications.notify(reviewers, "verification", "Claim failed verification",
                                 f"Claim {claim.reference} on {claim.item.reference} used all verification attempts.",
                                 link=f"/security/claims/{claim.pk}/")
        challenge.save()
        claim.save(update_fields=["status", "verification_status", "updated_at"])
        audit.record(user, "verification.attempt", f"Verification attempt on {claim.reference}", target=claim,
                     request=request, passed=passed, attempt_number=challenge.attempts_used)
        if claim.status == Claim.Status.UNDER_REVIEW:
            notifications.notify([user], "verification", "Verification submitted",
                                 f"Your claim {claim.reference} is now with campus security for review.",
                                 link=f"/my/claims/{claim.pk}/")
    return {"passed": passed, "attempts_remaining": challenge.attempts_remaining,
            "locked": challenge.status == VerificationChallenge.Status.LOCKED}


def review_data(claim, viewer, request=None):
    """Full verification evidence for authorised security staff. Audited."""
    if not viewer.has_perm("lostfound.view_private_details"):
        raise PermissionDenied
    challenge = VerificationChallenge.objects.filter(claim=claim).first()
    all_details = list(PrivateDetail.vault.filter(item=claim.item))
    audit.record(viewer, "private_detail.viewed", f"Private details of {claim.item.reference} viewed for {claim.reference}",
                 target=claim.item, request=request, claim=claim.reference)
    detail_map = {d.pk: d for d in all_details}
    attempts = []
    if challenge:
        for attempt in challenge.attempts.all():
            responses = crypto.decrypt_json(attempt.responses_ciphertext)
            rows = []
            for pk in challenge.detail_ids:
                d = detail_map.get(pk)
                if d is None:
                    continue
                qs = attempt.question_scores.get(str(pk), {})
                rows.append({"question": d.decrypt_question(), "expected": d.decrypt_answer(), "kind": d.get_kind_display(),
                             "response": responses.get(str(pk), ""), "similarity": qs.get("similarity"),
                             "passed": qs.get("passed"), "mode": d.get_match_mode_display()})
            attempts.append({"attempt": attempt, "rows": rows})
    details = [{"kind": d.get_kind_display(), "question": d.decrypt_question(), "answer": d.decrypt_answer(),
                "mode": d.get_match_mode_display(), "weight": d.weight, "active": d.is_active,
                "drawn": bool(challenge and d.pk in challenge.detail_ids)}
               for d in all_details]
    return {"challenge": challenge, "attempts": attempts, "details": details}
