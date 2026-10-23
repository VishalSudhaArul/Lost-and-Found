"""Server-side authorisation helpers. Hiding a button is never authorisation:
every protected view uses one of these decorators, and every service function
re-checks the permission it needs."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def perm_required(*codenames):
    """Require ALL listed ``lostfound.<codename>`` permissions."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not all(request.user.has_perm(f"lostfound.{c}") for c in codenames):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def any_perm_required(*codenames):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not any(request.user.has_perm(f"lostfound.{c}") for c in codenames):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
