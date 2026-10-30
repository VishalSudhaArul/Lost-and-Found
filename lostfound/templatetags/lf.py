from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

# Badge tone per status value: lost/urgent = red, pending = amber, success = green.
STATUS_CLASS = {
    "open": "badge-lost", "in_custody": "badge-info", "awaiting_handover": "badge-warn", "returned": "badge-ok",
    "disposal_review": "badge-warn", "disposed": "", "closed": "",
    "pending_verification": "badge-warn", "under_review": "badge-warn", "info_requested": "badge-info", "approved": "badge-ok", "rejected": "badge-bad",
    "withdrawn": "", "completed": "badge-ok", "scheduled": "badge-info", "cancelled": "",
    "passed": "badge-ok", "failed": "badge-bad", "not_started": "",
    "suggested": "badge-info", "dismissed": "", "claimed": "badge-ok",
    "lost": "badge-lost", "found": "badge-found",
}

# Category keyword -> icon used for placeholders when an item has no photo.
CATEGORY_ICONS = (
    (("earphone", "headphone", "earbud", "charger"), "headphones"), (("phone",), "phone"),
    (("laptop", "tablet", "calculator"), "laptop"), (("electronic",), "headphones"),
    (("bag", "backpack", "pouch"), "backpack"), (("wallet", "purse"), "wallet"),
    (("id card", "card", "document"), "id-card"), (("cloth", "jacket", "cap", "shirt", "accessor"), "shirt"),
    (("bottle", "lunch"), "bottle"), (("key",), "key"), (("book", "stationery", "notebook"), "book"),
)


@register.simple_tag
def icon(name, cls=""):
    return format_html('<svg class="icon {}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
                       cls, name)


@register.filter
def category_icon(category):
    text = str(category or "").lower()
    for words, name in CATEGORY_ICONS:
        if any(w in text for w in words):
            return name
    return "package"


@register.inclusion_tag("partials/status_badge.html")
def badge(value, label=None):
    return {"tone": STATUS_CLASS.get(str(value), ""), "label": label or value}


@register.inclusion_tag("partials/field.html")
def field(bound_field, extra_class=""):
    return {"f": bound_field, "extra_class": extra_class}


@register.simple_tag(takes_context=True)
def nav_current(context, *names):
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    return mark_safe('aria-current="page"') if match and match.url_name in names else ""


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs):
    """Current query string with some keys replaced (used by view toggles and pagination)."""
    request = context.get("request")
    params = request.GET.copy() if request else None
    if params is None:
        return ""
    for key, value in kwargs.items():
        params.pop(key, None)
        if value not in (None, ""):
            params[key] = value
    if "page" not in kwargs:
        params.pop("page", None)
    return params.urlencode()


@register.filter
def pct(value):
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


@register.filter
def initials(user):
    name = (user.get_full_name() or user.get_username() or "?").split()
    return "".join(part[0] for part in name[:2]).upper()
