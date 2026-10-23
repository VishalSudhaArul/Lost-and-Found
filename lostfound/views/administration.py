"""Campus administrator and system administrator screens."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import roles
from ..forms import AttributeForm, CategoryForm, LocationForm, MatchingSettingsForm, RetentionSettingsForm, UserRolesForm
from ..models import AuditEntry, CategoryAttribute, ItemCategory, Location, OperationalSettings
from ..permissions import any_perm_required, perm_required
from ..services import analytics, audit, matching

User = get_user_model()


@perm_required("view_analytics")
def analytics_view(request):
    return render(request, "administration/analytics.html", analytics.admin_metrics())


@any_perm_required("manage_taxonomy", "manage_retention", "manage_matching", "manage_users", "view_analytics")
def overview(request):
    cfg = OperationalSettings.load()
    return render(request, "administration/overview.html", {
        "cfg": cfg, "category_count": ItemCategory.objects.count(), "location_count": Location.objects.count(),
        "user_count": User.objects.count(), "metrics": analytics.admin_metrics() if request.user.has_perm("lostfound.view_analytics") else None})


def _save_form(request, form, label, action):
    obj = form.save()
    audit.record(request.user, action, f"{label} '{obj}' saved", target=obj, request=request)
    messages.success(request, f"{label} saved.")
    return obj


@perm_required("manage_taxonomy")
def categories(request, pk=None):
    instance = get_object_or_404(ItemCategory, pk=pk) if pk else None
    form = CategoryForm(request.POST or None, instance=instance, prefix="cat")
    attr_form = AttributeForm(request.POST or None if "attr-key" in request.POST else None, prefix="attr",
                              initial={"category": pk})
    if request.method == "POST":
        if "attr-key" in request.POST:
            if attr_form.is_valid():
                _save_form(request, attr_form, "Attribute", "taxonomy.attribute_saved")
                return redirect(request.path)
        elif form.is_valid():
            obj = _save_form(request, form, "Category", "taxonomy.category_saved")
            return redirect("admin_category_edit", pk=obj.pk)
    tree = ItemCategory.objects.filter(parent__isnull=True).prefetch_related("children", "attributes")
    return render(request, "administration/categories.html", {
        "form": form, "attr_form": attr_form, "tree": tree, "editing": instance,
        "attributes": CategoryAttribute.objects.filter(category=instance) if instance else None})


@perm_required("manage_taxonomy")
def locations(request, pk=None):
    instance = get_object_or_404(Location, pk=pk) if pk else None
    form = LocationForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = _save_form(request, form, "Location", "taxonomy.location_saved")
        return redirect("admin_location_edit", pk=obj.pk)
    return render(request, "administration/locations.html", {
        "form": form, "editing": instance, "locations": Location.objects.all()})


@any_perm_required("manage_retention", "manage_matching")
def settings_view(request):
    cfg = OperationalSettings.load()
    can_retention = request.user.has_perm("lostfound.manage_retention")
    can_matching = request.user.has_perm("lostfound.manage_matching")
    retention_form = RetentionSettingsForm(request.POST if "retention" in request.POST else None, instance=cfg, prefix="ret")
    matching_form = MatchingSettingsForm(request.POST if "matching" in request.POST else None, instance=cfg, prefix="mat")
    if request.method == "POST":
        form, allowed, label = ((retention_form, can_retention, "Retention policy") if "retention" in request.POST
                                else (matching_form, can_matching, "Matching and verification settings"))
        if not allowed:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if form.is_valid():
            form.save()
            audit.record(request.user, "settings.updated", f"{label} updated", target=cfg, request=request,
                         fields=sorted(form.changed_data))
            messages.success(request, f"{label} saved.")
            return redirect("admin_settings")
    return render(request, "administration/settings.html", {
        "retention_form": retention_form, "matching_form": matching_form,
        "can_retention": can_retention, "can_matching": can_matching})


@perm_required("manage_matching")
@require_POST
def run_matching(request):
    count = matching.refresh_all()
    audit.record(request.user, "matching.refreshed", f"Manual match refresh ({count} suggestions)", request=request)
    messages.success(request, f"Matching refreshed: {count} suggestion(s) at or above the threshold.")
    return redirect("admin_settings")


@perm_required("manage_users")
def users(request):
    q = request.GET.get("q", "").strip()
    qs = User.objects.prefetch_related("groups").order_by("username")
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    role = request.GET.get("role")
    if role:
        qs = qs.filter(groups__name=role)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "administration/users.html", {"page": page, "q": q, "role": role,
                                                         "roles": roles.ROLE_DESCRIPTIONS})


@perm_required("manage_users")
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    form = UserRolesForm(request.POST or None, initial={
        "roles": list(target.groups.values_list("name", flat=True)), "is_active": target.is_active})
    if request.method == "POST" and form.is_valid():
        new_roles = set(form.cleaned_data["roles"])
        if target.pk == request.user.pk and (roles.SYSTEM_ADMIN not in new_roles or not form.cleaned_data["is_active"]) \
                and not request.user.is_superuser:
            messages.error(request, "You cannot remove your own administrator access or deactivate yourself.")
        else:
            target.groups.set(Group.objects.filter(name__in=new_roles))
            target.is_active = form.cleaned_data["is_active"]
            target.save(update_fields=["is_active"])
            audit.record(request.user, "users.roles_changed", f"Roles for {target.username} updated", target=target,
                         request=request, roles=sorted(new_roles), active=target.is_active)
            messages.success(request, f"Roles for {target.username} updated.")
            return redirect("admin_users")
    return render(request, "administration/user_edit.html", {"target": target, "form": form,
                                                             "descriptions": roles.ROLE_DESCRIPTIONS})


@perm_required("view_audit")
def audit_log(request):
    qs = AuditEntry.objects.select_related("actor")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(summary__icontains=q))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "administration/audit.html", {"page": page, "q": q})
