"""Field encryption using the maintained ``cryptography`` package (Fernet:
AES-128-CBC + HMAC-SHA256). Keys come only from FIELD_ENCRYPTION_KEYS.
MultiFernet allows key rotation: the first key encrypts, all keys decrypt."""
import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_cache = {}


def _fernet():
    keys = tuple(settings.FIELD_ENCRYPTION_KEYS)
    if not keys:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEYS is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    if keys not in _cache:
        try:
            fernet = MultiFernet([Fernet(k.encode()) for k in keys])
        except (ValueError, TypeError) as exc:
            raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS contains an invalid Fernet key.") from exc
        _cache.clear()
        _cache[keys] = fernet
    return _cache[keys]


def encrypt_text(text: str) -> str:
    return _fernet().encrypt((text or "").encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured("Could not decrypt a private field — wrong or rotated-out key.") from exc


def encrypt_json(data) -> str:
    return encrypt_text(json.dumps(data))


def decrypt_json(token: str):
    return json.loads(decrypt_text(token))


def rotate(token: str) -> str:
    return _fernet().rotate(token.encode("ascii")).decode("ascii")
