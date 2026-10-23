# Data model

```
ItemCategory (self-FK parent, 2 levels) ──< CategoryAttribute
Location (self-FK parent: area › building › floor › room; is_custody_point)

Item (report_type lost|found, status, attributes JSON, photo, reporter → User)
  ├── category → ItemCategory (PROTECT)      ├── location → Location (PROTECT)
  ├── current_custody_location → Location   └── disposal_reviewed_by → User
PrivateDetail (item → Item, related_name="+", encrypted question/answer, match_mode, weight)
MatchSuggestion (lost_item, found_item → Item, score 0..1, components JSON) unique(lost, found)
Claim (item → Item, claimant → User, lost_report → Item?, status, verification_status)
  └── VerificationChallenge 1:1 (detail_ids, attempts_used ≤ max_attempts) ──< VerificationAttempt (encrypted responses)
  └── Handover 1:1 (officer, schedule, OTP hash/expiry/attempts, signature, proof, receipt)
CustodyRecord (item, action, from/to location, accepted_by, corrects → CustodyRecord)   append-only
DisposalRecord 1:1 Item (method, reason, reviewed_by, authorized_by, evidence)
AuditEntry (actor, action, target, metadata)   append-only
Notification (user, kind, dedupe_key)  unique(user, dedupe_key) when set
OperationalSettings (singleton: retention, matching weights, verification/OTP limits)
UserProfile 1:1 User (student/staff, department)
RolePermissions (unmanaged; holds custom permissions assigned to groups)
```

## Key constraints

| Constraint | Purpose |
|---|---|
| `uniq_active_claim_per_claimant` (partial) | one live claim per person per item |
| `uniq_approved_claim_per_item` (partial) | never two approved/completed claims for an item |
| `lost_item_status_valid` (check) | lost reports only use open/returned/closed |
| `completed_handover_has_evidence` (check) | a completed handover always has a time and an ID check |
| `challenge_attempts_within_limit` (check) | attempts can't exceed the maximum |
| `match_score_range` (check) | score between 0 and 1 |
| PROTECT on every FK to users/items/locations from history tables | deleting a user or item can't erase custody, claims or audit |
| PostgreSQL triggers `custodyrecord_append_only`, `auditentry_append_only` | UPDATE/DELETE rejected at database level |

Indexes: composite indexes on item type/status/date, category, location/time; claim status; notification unread; and on PostgreSQL a GIN trigram index on `Item.search_text` and a GIN full-text index on title + search text.
