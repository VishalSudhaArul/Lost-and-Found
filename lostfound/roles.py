"""Role definitions (Django Groups) and their permissions.

Roles are additive groups. A user may hold several (e.g. Security + Campus
Administrator). Everyone who registers joins MEMBERS."""
from django.contrib.auth.models import Group, Permission

MEMBERS = "Students & Staff"
SECURITY = "Security Personnel"
CAMPUS_ADMIN = "Campus Administrator"
SYSTEM_ADMIN = "System Administrator"

ROLE_PERMISSIONS = {
    MEMBERS: [],
    SECURITY: [
        "access_security_dashboard", "review_claims", "decide_claims", "view_private_details", "manage_custody",
        "conduct_handover", "review_disposal", "view_audit", "review_reports",
    ],
    CAMPUS_ADMIN: [
        "access_security_dashboard", "manage_taxonomy", "manage_retention", "view_analytics", "view_audit",
        "authorize_disposal", "review_disposal", "manage_rewards", "review_reports",
    ],
    SYSTEM_ADMIN: [
        "access_security_dashboard", "manage_matching", "manage_users", "view_analytics", "view_audit",
        "manage_taxonomy",
    ],
}

ROLE_DESCRIPTIONS = {
    MEMBERS: "Report items, browse the board, submit claims.",
    SECURITY: "Lost & Found team: verifies reports, custody, claim decisions, supervised handovers.",
    CAMPUS_ADMIN: "Categories, locations, retention, rewards policy, disposal authorisation, analytics.",
    SYSTEM_ADMIN: "Users and roles, matching and verification configuration.",
}


def ensure_roles(**kwargs):
    for name, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=name)
        perms = Permission.objects.filter(content_type__app_label="lostfound", codename__in=codenames)
        if perms.count() != len(codenames):
            continue  # permissions not created yet (first migrate); post_migrate runs again
        group.permissions.set(perms)


def user_roles(user):
    if not user.is_authenticated:
        return []
    return list(user.groups.values_list("name", flat=True))
