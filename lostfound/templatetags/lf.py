from django import template

register = template.Library()

STATUS_CLASS = {
    "open": "badge-lost", "in_custody": "badge-found", "awaiting_handover": "badge-warn", "returned": "badge-ok",
    "disposal_review": "badge-warn", "disposed": "", "closed": "",
    "pending_verification": "badge-lost", "under_review": "badge-warn", "approved": "badge-ok", "rejected": "badge-bad",
    "withdrawn": "", "completed": "badge-ok", "scheduled": "badge-warn", "cancelled": "",
    "passed": "badge-ok", "failed": "badge-bad", "not_started": "",
    "suggested": "badge-lost", "dismissed": "", "claimed": "badge-ok",
}


@register.simple_tag
def badge(value, label=None):
    from django.utils.html import format_html
    return format_html('<span class="badge {}">{}</span>', STATUS_CLASS.get(str(value), ""), label or value)


@register.inclusion_tag("partials/field.html")
def field(bound_field, extra_class=""):
    return {"f": bound_field, "extra_class": extra_class}


@register.simple_tag(takes_context=True)
def nav_current(context, *names):
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    from django.utils.safestring import mark_safe
    return mark_safe('aria-current="page"') if match and match.url_name in names else ""


@register.filter
def pct(value):
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0
