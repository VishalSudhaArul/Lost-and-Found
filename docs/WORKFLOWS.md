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
pending_verification ──(passes challenge)──> under_review[passed] ──(security approves)──> approved ──(handover)──> completed
        │  (attempts exhausted) ──────────> under_review[failed] ──(security rejects)──> rejected
        └─ any active state ──(claimant)──> withdrawn          approved ──(rejected)──> rejected (item back to custody)
```
* Approval requires: `under_review`, verification **passed**, item in custody, decided by someone other than the claimant, and explicit confirmation if competing claims exist (they are rejected with a reason).
* If an item has no private details yet, the claim waits; when security adds one, waiting claims get their challenge.

## Handover
`scheduled → completed` or `scheduled → cancelled` (can be rescheduled). Completion requires the claim approved, verification passed, item awaiting handover, an ID check confirmation, and either a valid OTP or a drawn signature. It appends a `released` custody record and marks the linked lost report returned.

## Disposal
Daily `check_ageing` moves expired, unclaimed items to `disposal_review`. Security records a review (physical check) → a user with `authorize_disposal` (campus administrator) records the method, reason and optional evidence. Items with active or completed claims are refused; records are never deleted.
