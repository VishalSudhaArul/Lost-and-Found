"""Community rewards (public explainer + personal dashboard), leaderboard and profile."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from ..forms import ProfileForm
from ..models import AchievementBadge, UserProfile
from ..services import audit, rewards


@require_GET
def rewards_page(request):
    """Open to everyone: how recognition works. Signed-in members also see their own progress."""
    context = {
        "policy": rewards.policy(),
        "tiers": AchievementBadge.objects.filter(kind=AchievementBadge.Kind.TIER, is_active=True),
        "badges": AchievementBadge.objects.filter(kind=AchievementBadge.Kind.BADGE, is_active=True),
    }
    if request.user.is_authenticated:
        context["me"] = rewards.summary(request.user)
        context["leaders"] = rewards.leaderboard(limit=5)
    return render(request, "rewards/rewards.html", context)


@login_required
@require_GET
def leaderboard(request):
    rows = rewards.leaderboard(limit=50)
    profile = rewards.profile_for(request.user)
    return render(request, "rewards/leaderboard.html", {
        "rows": rows, "policy": rewards.policy(), "profile": profile,
        "my_row": next((r for r in rows if r["user_id"] == request.user.pk), None)})


@login_required
def profile(request):
    user = request.user
    member, _ = UserProfile.objects.get_or_create(user=user)
    reward_profile = rewards.profile_for(user)
    initial = {"first_name": user.first_name, "last_name": user.last_name, "department": member.department,
               "display_name": reward_profile.display_name, "show_on_leaderboard": reward_profile.show_on_leaderboard,
               "user": user}
    form = ProfileForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user.first_name, user.last_name = data["first_name"], data["last_name"]
            user.save(update_fields=["first_name", "last_name"])
            member.department = data["department"]
            member.save(update_fields=["department", "updated_at"])
            reward_profile.display_name = data["display_name"]
            reward_profile.show_on_leaderboard = data["show_on_leaderboard"]
            reward_profile.save()
        audit.record(user, "profile.updated", "Profile and privacy preferences updated", request=request,
                     leaderboard=data["show_on_leaderboard"])
        messages.success(request, "Profile saved.")
        return redirect("profile")
    return render(request, "member/profile.html", {"form": form, "member": member, "reward_profile": reward_profile})
