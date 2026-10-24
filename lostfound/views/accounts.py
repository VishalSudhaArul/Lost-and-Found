"""Sign-in and registration.

* Students and teachers each have their own portal (sign-in + registration). A
  portal only accepts accounts of its own type.
* The Lost & Found team and administrators use a separate staff sign-in with a
  second step: a one-time code emailed to the account owner.
* Team and administrator accounts can't self-register. An administrator creates
  them (re-entering their own password) and the person activates the account from
  a one-time link sent to their own email, choosing their own password."""
import secrets
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView
from django.core.mail import send_mail
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from ..forms import (RateLimitedLoginForm, RegistrationForm, StaffCodeForm, StaffLoginForm, StudentLoginForm,
                     TeacherLoginForm)
from ..models import UserProfile
from ..services import audit

User = get_user_model()
BACKEND = "django.contrib.auth.backends.ModelBackend"
PORTALS = {
    UserProfile.MemberType.STUDENT: {"label": "Student", "form": StudentLoginForm, "icon": "book"},
    UserProfile.MemberType.STAFF: {"label": "Teacher", "form": TeacherLoginForm, "icon": "users"},
}
PORTAL_SLUGS = {"student": UserProfile.MemberType.STUDENT, "teacher": UserProfile.MemberType.STAFF}


class SignInView(LoginView):
    """Landing sign-in: lets people pick their portal (still accepts student/teacher sign-in)."""
    template_name = "accounts/login.html"
    authentication_form = RateLimitedLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.record(self.request.user, "auth.login", "Signed in", request=self.request)
        return response


class PortalSignInView(SignInView):
    template_name = "accounts/portal_login.html"
    portal = None

    def get_form_class(self):
        return PORTALS[self.portal]["form"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(portal=self.portal, portal_label=PORTALS[self.portal]["label"],
                   portal_slug="student" if self.portal == UserProfile.MemberType.STUDENT else "teacher")
        return ctx


def _register(request, portal=None):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegistrationForm(request.POST or None, portal=portal)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend=BACKEND)
        audit.record(user, "auth.registered", f"{user.profile.get_member_type_display()} account created", request=request)
        messages.success(request, "Welcome! Your account is ready.")
        return redirect("dashboard")
    if portal:
        return render(request, "accounts/register.html", {
            "form": form, "portal": portal, "portal_label": PORTALS[portal]["label"],
            "portal_slug": "student" if portal == UserProfile.MemberType.STUDENT else "teacher"})
    if request.method == "POST":  # generic form (kept for older links)
        return render(request, "accounts/register.html", {"form": form})
    return render(request, "accounts/register_choose.html")


def register(request):
    return _register(request)


def register_portal(request, slug):
    if slug not in PORTAL_SLUGS:
        raise Http404
    return _register(request, PORTAL_SLUGS[slug])


# --- Staff sign-in: password, then an emailed one-time code ---------------------

SESSION_KEY = "staff_2fa"


def _digest(user_id, code):
    return salted_hmac("lostfound.staff-2fa", f"{user_id}:{code}").hexdigest()


def _mask(email):
    name, _, domain = email.partition("@")
    return f"{name[:1]}{'•' * max(len(name) - 1, 2)}@{domain}"


def _send_code(request, user, next_url):
    code = f"{secrets.randbelow(10 ** 6):06d}"
    request.session[SESSION_KEY] = {
        "uid": user.pk, "digest": _digest(user.pk, code), "exp": time.time() + settings.STAFF_CODE_TTL_SECONDS,
        "attempts": 0, "sent": time.time(), "next": next_url}
    minutes = settings.STAFF_CODE_TTL_SECONDS // 60
    send_mail("Your Campus Lost & Found staff sign-in code",
              f"Your one-time sign-in code is {code}.\n\nIt expires in {minutes} minutes. If you didn't try to "
              f"sign in, ignore this email and tell your administrator.",
              None, [user.email])
    audit.record(user, "auth.staff_code_sent", "Staff sign-in code emailed", request=request)


def _staff_home(user):
    if user.has_perm("lostfound.manage_users") or user.has_perm("lostfound.view_analytics"):
        return reverse("admin_overview")
    if user.has_perm("lostfound.access_security_dashboard"):
        return reverse("security_dashboard")
    return reverse("dashboard")


class StaffSignInView(SignInView):
    """Step 1: password. Member accounts are refused. A correct password does not sign
    the person in; it emails a one-time code to the account's own address."""
    template_name = "accounts/staff_login.html"
    authentication_form = StaffLoginForm

    def form_valid(self, form):
        user = form.get_user()
        if not user.email:
            form.add_error(None, "This account has no email address for the sign-in code. Ask an administrator to add one.")
            return self.form_invalid(form)
        _send_code(self.request, user, self.get_redirect_url())
        messages.info(self.request, f"We emailed a 6-digit code to {_mask(user.email)}.")
        return redirect("staff_login_verify")


@never_cache
@sensitive_post_parameters("code")
def staff_login_verify(request):
    """Step 2: the emailed code. Expires, is attempt-limited and works once."""
    pending = request.session.get(SESSION_KEY)
    if not pending:
        messages.error(request, "Start by entering your username and password.")
        return redirect("staff_login")
    user = User.objects.filter(pk=pending["uid"], is_active=True).first()
    if user is None or time.time() > pending["exp"]:
        request.session.pop(SESSION_KEY, None)
        messages.error(request, "That code has expired. Sign in again to get a new one.")
        return redirect("staff_login")
    if request.method == "POST" and request.POST.get("action") == "resend":
        if time.time() - pending.get("sent", 0) < 30:
            messages.error(request, "Please wait 30 seconds before asking for another code.")
        else:
            _send_code(request, user, pending.get("next", ""))
            messages.info(request, f"A new code was sent to {_mask(user.email)}.")
        return redirect("staff_login_verify")
    form = StaffCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if constant_time_compare(_digest(user.pk, form.cleaned_data["code"]), pending["digest"]):
            next_url = pending.get("next", "")
            request.session.pop(SESSION_KEY, None)
            login(request, user, backend=BACKEND)
            audit.record(user, "auth.login", "Signed in to the staff console (password + email code)", request=request)
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
                next_url = _staff_home(user)
            return redirect(next_url or _staff_home(user))
        pending["attempts"] += 1
        audit.record(user, "auth.staff_code_failed", "Incorrect staff sign-in code", request=request,
                     attempt=pending["attempts"])
        if pending["attempts"] >= settings.STAFF_CODE_MAX_ATTEMPTS:
            request.session.pop(SESSION_KEY, None)
            messages.error(request, "Too many incorrect codes. Sign in again to get a new one.")
            return redirect("staff_login")
        request.session[SESSION_KEY] = pending
        left = settings.STAFF_CODE_MAX_ATTEMPTS - pending["attempts"]
        form.add_error("code", f"That code isn't right. {left} attempt{'s' if left != 1 else ''} left.")
    return render(request, "accounts/staff_verify.html", {
        "form": form, "email": _mask(user.email), "minutes": settings.STAFF_CODE_TTL_SECONDS // 60})


def admin_site_login(request, extra_context=None):
    """Django's built-in admin site uses the same two-step staff sign-in."""
    from django.contrib.auth.views import redirect_to_login
    return redirect_to_login(request.get_full_path(), reverse("staff_login"))


# --- Account activation (accounts created by an administrator) ---------------------

@never_cache
@sensitive_post_parameters("new_password1", "new_password2")
def activate(request, uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    valid = user is not None and not user.is_active and not user.has_usable_password() \
        and default_token_generator.check_token(user, token)
    if not valid:
        return render(request, "accounts/activate.html", {"valid": False}, status=400)
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        user.is_active = True
        user.save(update_fields=["is_active"])
        audit.record(user, "auth.activated", "Account activated from the emailed link", request=request)
        is_staff_role = user.groups.exclude(name="Students & Staff").exists() or user.is_superuser
        messages.success(request, "Your account is active. Sign in with your new password.")
        if is_staff_role:
            return redirect("staff_login")
        member_type = getattr(getattr(user, "profile", None), "member_type", "student")
        return redirect("teacher_login" if member_type == UserProfile.MemberType.STAFF else "student_login")
    return render(request, "accounts/activate.html", {"valid": True, "form": form, "target": user})
