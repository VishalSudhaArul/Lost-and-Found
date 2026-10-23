from django.conf import settings


def navigation(request):
    user = getattr(request, "user", None)
    data = {"campus_map": settings.CAMPUS_MAP, "unread_count": 0, "nav": {}}
    if user is not None and user.is_authenticated:
        perms = {c for c in ("access_security_dashboard", "review_claims", "manage_custody", "conduct_handover",
                             "review_disposal", "manage_taxonomy", "manage_retention", "manage_matching",
                             "manage_users", "view_analytics", "view_audit") if user.has_perm(f"lostfound.{c}")}
        data["nav"] = {p: True for p in perms}
        data["nav"]["is_staff_role"] = bool(perms)
        data["unread_count"] = user.notifications.filter(is_read=False).count()
    return data
