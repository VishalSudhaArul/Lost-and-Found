"""Fixed-window rate limiter on Django's cache."""
import hashlib

from django.core.cache import cache


class RateLimited(Exception):
    pass


def _key(scope, ident):
    return "rl:" + scope + ":" + hashlib.sha256(str(ident).encode()).hexdigest()[:32]


def hit(scope, ident, limit, window):
    """Count one event; raise RateLimited if the limit is exceeded."""
    key = _key(scope, ident)
    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:  # expired between add and incr
            cache.set(key, 1, timeout=window)
            count = 1
    if count > limit:
        raise RateLimited(scope)
    return count


def is_limited(scope, ident, limit):
    return (cache.get(_key(scope, ident)) or 0) >= limit


def reset(scope, ident):
    cache.delete(_key(scope, ident))
