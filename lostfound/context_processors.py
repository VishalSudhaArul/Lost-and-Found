from django.conf import settings

# Section label shown in the top bar, keyed by URL-name prefix.
SECTIONS = (
    (("security_",), "Lost & Found team"),
    (("admin_",), "Administration"),
    (("my_", "report_", "claim_", "handover_code", "notification", "dashboard", "profile"), "My account"),
    (("rewards", "leaderboard"), "Community rewards"),
    (("board", "item_detail"), "Lost & found board"),
    (("password_",), "Account"),
)


def _section(request):
    match = getattr(request, "resolver_match", None)
    name = getattr(match, "url_name", "") or ""
    for prefixes, label in SECTIONS:
        if name.startswith(prefixes):
            return label
    return ""


def navigation(request):
    user = getattr(request, "user", None)
    data = {"campus_map": settings.CAMPUS_MAP, "unread_count": 0, "nav": {}, "section": _section(request)}
    if user is not None and user.is_authenticated:
        perms = {c for c in ("access_security_dashboard", "review_claims", "manage_custody", "conduct_handover",
                             "review_disposal", "manage_taxonomy", "manage_retention", "manage_matching",
                             "manage_users", "view_analytics", "view_audit", "review_reports", "manage_rewards") if user.has_perm(f"lostfound.{c}")}
        data["nav"] = {p: True for p in perms}
        data["nav"]["is_staff_role"] = bool(perms)
        data["unread_count"] = user.notifications.filter(is_read=False).count()
        if "review_reports" in perms:
            from .models import Item
            data["pending_reviews"] = Item.objects.filter(review_status=Item.Review.PENDING).count()
    return data
