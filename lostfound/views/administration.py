"""Campus administrator and system administrator screens."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import roles
from ..forms import (AdminCreateUserForm, AttributeForm, BadgeForm, CategoryForm, CommunityAwardForm, LedgerReverseForm,
                     LocationForm, MatchingSettingsForm, RetentionSettingsForm, RewardPolicyForm, UserDetailsForm,
                     UserRolesForm)
from ..models import (AchievementBadge, AuditEntry, CategoryAttribute, Claim, Item, ItemCategory, Location,
                      OperationalSettings, PointsLedgerEntry, UserProfile)
from ..services.errors import WorkflowError
from ..permissions import any_perm_required, perm_required
from ..services import analytics, audit, matching, rewards

User = get_user_model()


@perm_required("view_analytics")
def analytics_view(request):
    return render(request, "administration/analytics.html", analytics.admin_metrics())


@any_perm_required("manage_taxonomy", "manage_retention", "manage_matching", "manage_users", "view_analytics")
def overview(request):
    cfg = OperationalSettings.load()
    return render(request, "administration/overview.html", {
        "cfg": cfg, "category_count": ItemCategory.objects.count(), "location_count": Location.objects.count(),
        "user_count": User.objects.count(), "counts": analytics.overview_counts(),
        "metrics": analytics.admin_metrics() if request.user.has_perm("lostfound.view_analytics") else None})


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


ROLE_TABS = {
    "students": ("Students", Q(profile__member_type=UserProfile.MemberType.STUDENT)),
    "staff": ("Staff members", Q(profile__member_type=UserProfile.MemberType.STAFF)),
    "team": ("Lost & Found team", Q(groups__name=roles.SECURITY)),
    "admins": ("Administrators", Q(groups__name__in=[roles.CAMPUS_ADMIN, roles.SYSTEM_ADMIN]) | Q(is_superuser=True)),
    "inactive": ("Inactive", Q(is_active=False)),
}


@perm_required("manage_users")
def users(request):
    q = request.GET.get("q", "").strip()
    tab = request.GET.get("tab", "")
    qs = User.objects.select_related("profile").prefetch_related("groups").order_by("username")
    if tab in ROLE_TABS:
        qs = qs.filter(ROLE_TABS[tab][1]).distinct()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                       | Q(last_name__icontains=q))
    counts = {key: User.objects.filter(cond).distinct().count() for key, (_, cond) in ROLE_TABS.items()}
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "administration/users.html", {
        "page": page, "q": q, "tab": tab, "tabs": [(k, label, counts[k]) for k, (label, _) in ROLE_TABS.items()],
        "total": User.objects.count(), "querystring": f"tab={tab}&q={q}"})


def send_activation(request, user):
    """Email a one-time activation link to the account's own address."""
    from django.conf import settings as dj_settings
    from django.contrib.auth.tokens import default_token_generator
    from django.core.mail import send_mail
    from django.template.loader import render_to_string
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode
    staff_roles = [g for g in user.groups.values_list("name", flat=True) if g != roles.MEMBERS]
    body = render_to_string("accounts/activation_email.txt", {
        "user": user, "uid": urlsafe_base64_encode(force_bytes(user.pk)),
        "token": default_token_generator.make_token(user), "protocol": "https" if request.is_secure() else "http",
        "domain": request.get_host(), "roles": ", ".join(staff_roles),
        "days": max(1, dj_settings.PASSWORD_RESET_TIMEOUT // 86400)})
    send_mail("Activate your Campus Lost & Found account", body, None, [user.email])


@perm_required("manage_users")
def user_create(request):
    form = AdminCreateUserForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        send_activation(request, user)
        audit.record(request.user, "users.created", f"Account {user.username} created; activation link emailed",
                     target=user, request=request, roles=sorted(form.cleaned_data["roles"]))
        messages.success(request, f"Account {user.username} created and locked. An activation link was sent to "
                                  f"{user.email}; the account works only after they open it and choose a password.")
        return redirect("admin_user_edit", pk=user.pk)
    return render(request, "administration/user_create.html", {"form": form, "descriptions": roles.ROLE_DESCRIPTIONS})


@perm_required("manage_users")
def user_edit(request, pk):
    target = get_object_or_404(User, pk=pk)
    member, _ = UserProfile.objects.get_or_create(user=target)
    action = request.POST.get("action", "roles") if request.method == "POST" else None
    roles_form = UserRolesForm(request.POST if action == "roles" else None, initial={
        "roles": list(target.groups.values_list("name", flat=True)), "is_active": target.is_active})
    details_form = UserDetailsForm(request.POST if action == "details" else None, target=target, initial={
        "first_name": target.first_name, "last_name": target.last_name, "email": target.email,
        "member_type": member.member_type, "department": member.department})
    if action == "roles" and roles_form.is_valid():
        from ..forms import STAFF_ROLES, check_staff_email
        from django.core.exceptions import ValidationError
        new_roles = set(roles_form.cleaned_data["roles"])
        current = set(target.groups.values_list("name", flat=True))
        granting_staff = bool((new_roles - current) & set(STAFF_ROLES))
        removing_self = target.pk == request.user.pk and (
            roles.SYSTEM_ADMIN not in new_roles or not roles_form.cleaned_data["is_active"])
        email_problem = None
        if granting_staff:
            try:
                check_staff_email(target.email or "", new_roles)
            except ValidationError as exc:
                email_problem = exc.messages[0]
        if removing_self and not request.user.is_superuser:
            messages.error(request, "You cannot remove your own administrator access or deactivate yourself.")
        elif granting_staff and not request.user.check_password(roles_form.cleaned_data.get("confirm_password") or ""):
            messages.error(request, "Enter your own password to grant Lost & Found team or administrator access.")
        elif granting_staff and email_problem:
            messages.error(request, email_problem)
        else:
            target.groups.set(Group.objects.filter(name__in=new_roles))
            target.is_active = roles_form.cleaned_data["is_active"]
            target.save(update_fields=["is_active"])
            audit.record(request.user, "users.roles_changed", f"Roles for {target.username} updated", target=target,
                         request=request, roles=sorted(new_roles), active=target.is_active)
            messages.success(request, f"Roles and status for {target.username} saved.")
            return redirect("admin_user_edit", pk=target.pk)
    elif action == "details" and details_form.is_valid():
        data = details_form.cleaned_data
        target.first_name, target.last_name, target.email = data["first_name"], data["last_name"], data["email"]
        target.save(update_fields=["first_name", "last_name", "email"])
        member.member_type, member.department = data["member_type"], data["department"]
        member.save(update_fields=["member_type", "department", "updated_at"])
        audit.record(request.user, "users.details_changed", f"Details for {target.username} updated", target=target,
                     request=request, fields=sorted(details_form.changed_data))
        messages.success(request, f"Details for {target.username} saved.")
        return redirect("admin_user_edit", pk=target.pk)
    elif action == "reset" and not target.is_active and not target.has_usable_password() and target.email:
        send_activation(request, target)
        audit.record(request.user, "users.activation_resent", f"Activation link re-sent to {target.username}",
                     target=target, request=request)
        messages.success(request, f"A new activation link was sent to {target.email}.")
        return redirect("admin_user_edit", pk=target.pk)
    elif action == "reset":
        from django.contrib.auth.forms import PasswordResetForm
        reset = PasswordResetForm({"email": target.email})
        if target.email and target.is_active and reset.is_valid():
            reset.save(request=request, use_https=request.is_secure(),
                       email_template_name="accounts/password_reset_email.txt",
                       subject_template_name="accounts/password_reset_subject.txt")
            audit.record(request.user, "users.reset_sent", f"Password reset link sent to {target.username}",
                         target=target, request=request)
            messages.success(request, f"A password reset link was sent to {target.email}.")
        else:
            messages.error(request, "This account needs to be active and have an email address.")
        return redirect("admin_user_edit", pk=target.pk)
    activity = {
        "reports": Item.objects.filter(reporter=target).count(),
        "claims": Claim.objects.filter(claimant=target).count(),
        "points": rewards.summary(target)["balance"],
    }
    return render(request, "administration/user_edit.html", {
        "awaiting_activation": not target.is_active and not target.has_usable_password(),
        "target": target, "member": member, "form": roles_form, "details_form": details_form,
        "descriptions": roles.ROLE_DESCRIPTIONS, "activity": activity})


@perm_required("manage_rewards")
def rewards_admin(request):
    cfg = rewards.policy()
    action = request.POST.get("action") if request.method == "POST" else None
    policy_form = RewardPolicyForm(request.POST if action == "policy" else None, instance=cfg, prefix="pol")
    award_form = CommunityAwardForm(request.POST if action == "award" else None, prefix="aw")
    if action == "policy" and policy_form.is_valid():
        policy_form.save()
        audit.record(request.user, "rewards.policy_updated", "Reward policy updated", target=cfg, request=request,
                     fields=sorted(policy_form.changed_data))
        messages.success(request, "Reward policy saved. New values apply to future awards only.")
        return redirect("admin_rewards")
    if action == "award" and award_form.is_valid():
        try:
            rewards.award_community(award_form.cleaned_data["username"], award_form.cleaned_data["points"],
                                    award_form.cleaned_data["reason"], request.user, request=request)
            messages.success(request, "Community points recorded.")
            return redirect("admin_rewards")
        except WorkflowError as exc:
            messages.error(request, str(exc))
    totals = PointsLedgerEntry.objects.aggregate(awarded=Sum("points", filter=Q(points__gt=0)),
                                                 corrected=Sum("points", filter=Q(points__lt=0)))
    return render(request, "administration/rewards.html", {
        "policy_form": policy_form, "award_form": award_form, "totals": totals,
        "badges": AchievementBadge.objects.annotate(holders_count=Count("holders")),
        "entry_count": PointsLedgerEntry.objects.count(), "leaders": rewards.leaderboard(limit=5)})


@perm_required("manage_rewards")
def badge_edit(request, pk):
    badge = get_object_or_404(AchievementBadge, pk=pk)
    form = BadgeForm(request.POST or None, instance=badge)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit.record(request.user, "rewards.badge_updated", f"Badge '{badge.name}' updated", target=badge,
                     request=request, fields=sorted(form.changed_data))
        messages.success(request, f"{badge.name} saved. Badges members already earned are kept.")
        return redirect("admin_rewards")
    return render(request, "administration/badge_edit.html", {"form": form, "badge": badge})


@perm_required("manage_rewards")
def rewards_ledger(request):
    if request.method == "POST":
        form = LedgerReverseForm(request.POST)
        if form.is_valid():
            try:
                rewards.reverse_entry(int(request.POST.get("entry", 0)), request.user, form.cleaned_data["reason"],
                                      request=request)
                messages.success(request, "Entry reversed. The correction is recorded in the ledger and audit log.")
            except PointsLedgerEntry.DoesNotExist:
                messages.error(request, "Entry not found.")
            except (WorkflowError, ValueError) as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, "Give a reason for the correction (at least 5 characters).")
        return redirect(request.get_full_path())
    qs = PointsLedgerEntry.objects.select_related("user", "item", "awarded_by", "reverses").prefetch_related("reversal")
    q = request.GET.get("q", "").strip()
    cat = request.GET.get("category", "")
    if q:
        qs = qs.filter(Q(user__username__icontains=q) | Q(reason__icontains=q) | Q(reference__icontains=q))
    if cat in PointsLedgerEntry.Category.values:
        qs = qs.filter(category=cat)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "administration/rewards_ledger.html", {
        "page": page, "q": q, "category": cat, "categories": PointsLedgerEntry.Category.choices,
        "querystring": f"q={q}&category={cat}"})


@perm_required("view_audit")
def audit_log(request):
    qs = AuditEntry.objects.select_related("actor")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(summary__icontains=q))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "administration/audit.html", {"page": page, "q": q})
