# Workflows and status transitions

## Found item
```
open ──(security receives)──> in_custody ──(claim approved)──> awaiting_handover ──(handover done)──> returned
  │                              │   ▲                              │
  └─(finder closes)─> closed     │   └──(claim rejected/withdrawn)──┘
                                 └─(retention expired, no active claim)─> disposal_review ──(authorised)──> disposed
                                                       ▲      │  (extend retention) ──> in_custody
                                                       └──────┘  (claim approved)   ──> awaiting_handover
```
Transitions are defined in `Item.TRANSITIONS`; `Item.transition_to()` refuses anything else.

## Lost report
`open → returned` (when a linked claim's handover completes) or `open → closed` (owner closes); `closed → open` allowed.

## Claim
```
under_review ("Pending review") ──(desk visit: officer verifies in person and approves)──> approved ──(handover)──> completed
      │  ⇅ info_requested (staff ask, claimant replies)
      ├──(officer rejects with a reason)──> rejected
      └─ any active state ──(claimant)──> withdrawn        approved ──(rejected)──> rejected (item back to custody)
```
* Verification is **staff-led and in person**: the claimant visits the Lost & Found / Security Desk, the officer questions them against the private details and records the decision in the portal.
* Approval requires: the claim under review, an authorised officer (`decide_claims`) other than the claimant, the in-person confirmation tick, a reason, the item in custody, and explicit confirmation if competing claims exist (they are rejected with a reason). It sets `verification_status = passed` and writes `claim.approved` to the audit log with `verification: in_person`, the reason and the time.
* Optional online questions (legacy `pending_verification` path) are supporting information only: passing never approves or verifies, failing never rejects; either way the claim goes to staff review.

## Handover
`scheduled → completed` or `scheduled → cancelled` (can be rescheduled). Completion requires the claim approved (which implies the in-person verification), item awaiting handover, an ID check confirmation, and either a valid OTP or a drawn signature. It appends a `released` custody record and marks the linked lost report returned.

## Disposal
Daily `check_ageing` moves expired, unclaimed items to `disposal_review`. Security records a review (physical check) → a user with `authorize_disposal` (campus administrator) records the method, reason and optional evidence. Items with active or completed claims are refused; records are never deleted.
