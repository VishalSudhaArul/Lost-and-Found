# Custody chain and corrections

Every physical movement of a found item creates a `CustodyRecord`: `received`, `transferred`, `released` (to the verified owner, with the handover receipt), `disposed`, or `correction`.

Records cannot be edited or deleted: the model raises on `save()` of an existing row and on `delete()`, the Django admin is read-only, and on PostgreSQL a trigger rejects `UPDATE` and `DELETE`.

## Correcting a mistake
On the item's security page, open the wrong entry and choose **Record a correction**, describing what was wrong. This appends a `correction` record linked to the original (`corrects`). The original stays visible, so the history shows both what was recorded and how it was corrected. Each correction is also in the audit log.

If an item was physically moved without being recorded, record a new **Transfer** with a note instead.
