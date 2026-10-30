# Public and private fields — statement for the campus security office

This document states, in plain terms, what the platform shows to whom. It is enforced in code by `lostfound/services/public.py` (an explicit allow-list) and proven by `lostfound/tests/test_privacy.py`.

## Shown publicly (anyone, no account needed)

For items in the states *Open*, *In custody* or *Awaiting handover* that staff have not hidden:

| Field | Example |
|---|---|
| Reference | F-00042 |
| Lost or found | Found |
| Title | Black backpack |
| General description | "Black backpack found on a bench." (written by the reporter, who is told not to include identifying details) |
| Category | Bags › Backpacks |
| Public attributes | Colour: black, Brand: Wildcraft |
| Location (campus place) | Campus › Library › Reading hall |
| Date and time lost/found | 23 Oct 2026, 14:10 |
| Status | In custody |
| Photo | Resized, re-encoded, with all metadata (including GPS) removed |

Returned, disposed and closed items leave the public board.

## Never shown publicly

* **Private details** of found items (distinguishing marks, contents, engravings, serial numbers) — questions and answers. Stored encrypted.
* Who reported the item (name, email), and who claimed it.
* The exact-spot note ("second row near the window").
* Claims, claim statements, decision reasons, verification answers and scores.
* Custody location history, handover times and receipts, OTP codes, signatures, proof photos.
* Disposal records and evidence.

## Who sees what inside the system

| Information | Reporter | Claimant | Security Personnel | Campus / System Admin |
|---|---|---|---|---|
| Own report, incl. exact spot | yes | — | yes | via security role only |
| Private detail questions | no (count only) | only the questions drawn for an optional online question set (never the answers) | yes, on the claim review screen, to question the claimant in person (audited) | only if also Security |
| Private detail answers | no | never | yes, on the claim review screen (audited) | only if also Security |
| Claimant's answers and per-question result | no | no (only pass/fail) | yes (audited) | only if also Security |
| Claim decision reason | no | yes | yes | — |
| Handover code | — | shown once on their phone | never (only entered and checked) | never |
| Custody chain | status only | status only | yes | audit log |

Every time staff open verification evidence an audit entry `private_detail.viewed` is written with who, when, which item and claim.
