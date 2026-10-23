import logging

from ..models.audit import AuditEntry

logger = logging.getLogger("lostfound.audit")

FORBIDDEN_METADATA_KEYS = {"password", "otp", "code", "answer", "answers", "responses", "key", "secret", "token"}


def client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def record(actor, action, summary, target=None, request=None, **metadata):
    bad = FORBIDDEN_METADATA_KEYS.intersection(k.lower() for k in metadata)
    if bad:
        raise ValueError(f"Refusing to audit sensitive keys: {sorted(bad)}")
    entry = AuditEntry.objects.create(
        actor=actor if (actor is not None and actor.is_authenticated) else None,
        action=action,
        target_type=target.__class__.__name__ if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        summary=summary[:300],
        metadata=metadata,
        ip_address=client_ip(request),
    )
    logger.info("audit %s by %s on %s:%s", action, getattr(actor, "pk", None), entry.target_type, entry.target_id)
    return entry
