from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db.models import Q as models_Q
from django.utils import timezone

from . import roles
from .models import (AchievementBadge, CategoryAttribute, DisposalRecord, Handover, Item, ItemCategory, Location,
                     OperationalSettings, PrivateDetail, RewardPolicy, UserProfile, UserRewardProfile)
from .services import images, ratelimit

User = get_user_model()

DATETIME_INPUT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


# --- Accounts ----------------------------------------------------------------

class RegistrationForm(UserCreationForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(help_text="Use your university email address.")
    member_type = forms.ChoiceField(choices=UserProfile.MemberType.choices, label="I am a")
    department = forms.CharField(max_length=120, required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, portal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.portal = portal
        if portal:
            # The portal decides the account type; it can't be changed from the form.
            del self.fields["member_type"]
            self.fields["department"].label = "Department or course" if portal == "student" else "Department"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        domains = OperationalSettings.load().registration_domains()
        if domains and email.rsplit("@", 1)[-1] not in domains:
            raise ValidationError("Registration is limited to university email addresses.")
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        member_type = self.portal or cleaned.get("member_type")
        cfg = OperationalSettings.load()
        if member_type == UserProfile.MemberType.STUDENT and not cfg.allow_student_registration:
            raise ValidationError("Student self-registration is closed. Ask an administrator for an account.")
        if member_type == UserProfile.MemberType.STAFF and not cfg.allow_teacher_registration:
            raise ValidationError("Teacher self-registration is closed. Ask an administrator for an account.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            UserProfile.objects.create(user=user, member_type=self.portal or self.cleaned_data["member_type"],
                                       department=self.cleaned_data.get("department", ""))
            user.groups.add(Group.objects.get(name=roles.MEMBERS))
        return user


class RateLimitedLoginForm(AuthenticationForm):
    """Locks a username+IP pair after repeated failures."""

    def clean(self):
        limit, window = settings.LOGIN_RATE_LIMIT
        ident = f"{(self.data.get('username') or '').lower()}|{self.request.META.get('REMOTE_ADDR') if self.request else ''}"
        if ratelimit.is_limited("login", ident, limit):
            raise ValidationError("Too many failed sign-in attempts. Try again in 15 minutes.", code="rate_limited")
        try:
            cleaned = super().clean()
        except ValidationError:
            try:
                ratelimit.hit("login", ident, limit, window)
            except ratelimit.RateLimited:
                pass
            raise
        ratelimit.reset("login", ident)
        return cleaned


# --- Reporting -----------------------------------------------------------------

class CategoryChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return str(obj)


class ItemReportForm(forms.ModelForm):
    category = CategoryChoiceField(queryset=ItemCategory.objects.none(), empty_label="Choose a category")
    location = forms.ModelChoiceField(queryset=Location.objects.none(), empty_label="Choose where")
    photo = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"}))

    class Meta:
        model = Item
        fields = ["title", "category", "description", "occurred_at", "location", "location_note", "photo"]
        widgets = {
            "occurred_at": DATETIME_INPUT,
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, report_type=Item.ReportType.LOST, **kwargs):
        super().__init__(*args, **kwargs)
        self.report_type = report_type
        self.fields["category"].queryset = ItemCategory.objects.filter(is_active=True).select_related("parent")
        self.fields["location"].queryset = Location.objects.filter(is_active=True)
        self.fields["occurred_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"]
        if not self.is_bound and not self.instance.pk:
            self.initial.setdefault("occurred_at", timezone.localtime().strftime("%Y-%m-%dT%H:%M"))
        verb = "lose" if report_type == Item.ReportType.LOST else "find"
        self.fields["title"].widget.attrs["placeholder"] = "e.g. Black backpack, Casio calculator"
        self.fields["occurred_at"].label = f"When did you {verb} it?"
        self.fields["location"].label = f"Where did you {verb} it?"
        self.fields["location_note"].label = "Exact spot (optional)"
        self.fields["location_note"].widget.attrs["placeholder"] = "e.g. second row near the window"
        if report_type == Item.ReportType.FOUND:
            self.fields["description"].help_text = (
                "Shown publicly. Describe it in general terms only — keep stickers, contents, "
                "engravings and serial numbers for the private section below.")
        else:
            self.fields["description"].help_text = "Shown publicly. Don't include anything you'd want to use to prove ownership."

    def clean_occurred_at(self):
        value = self.cleaned_data["occurred_at"]
        if value > timezone.now() + timezone.timedelta(minutes=5):
            raise ValidationError("The date cannot be in the future.")
        if value < timezone.now() - timezone.timedelta(days=365):
            raise ValidationError("Reports must be within the last year.")
        return value

    def clean_photo(self):
        upload = self.cleaned_data.get("photo")
        if not upload:
            return None
        return images.process_upload(upload)

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        attrs = {}
        if category:
            for attr in category.all_attributes():
                value = (self.data.get(f"attr_{attr.key}") or "").strip()[:80]
                if not value:
                    continue
                if attr.kind == CategoryAttribute.Kind.CHOICE and value not in attr.choice_list:
                    self.add_error(None, f"Choose a valid option for {attr.label}.")
                    continue
                attrs[attr.key] = value
        cleaned["attributes"] = attrs
        return cleaned

    def save(self, reporter, commit=True):
        item = super().save(commit=False)
        item.report_type = self.report_type
        item.reporter = reporter
        item.attributes = self.cleaned_data.get("attributes", {})
        # ``photo`` is already a processed, EXIF-free ContentFile from clean_photo();
        # ModelForm assigns it and the storage backend writes it on save.
        if commit:
            item.save()
        return item


class PrivateDetailForm(forms.Form):
    kind = forms.ChoiceField(choices=PrivateDetail.Kind.choices, label="Type of detail")
    question = forms.CharField(max_length=200, label="Question to ask the claimant",
                               widget=forms.TextInput(attrs={"placeholder": "e.g. What sticker is on the lid?",
                                                             "data-private-question": ""}))
    answer = forms.CharField(max_length=300, label="Correct answer (kept private and encrypted)",
                             widget=forms.TextInput(attrs={"placeholder": "e.g. a yellow smiley face", "autocomplete": "off"}))
    match_mode = forms.ChoiceField(choices=PrivateDetail.MatchMode.choices, initial=PrivateDetail.MatchMode.KEYWORDS,
                                   label="How strictly to compare")

    def has_changed(self):
        # Selects always submit a value; a row only counts as filled in when
        # the finder typed a question or an answer.
        return any((self.data.get(self.add_prefix(n)) or "").strip() for n in ("question", "answer"))

    def clean(self):
        cleaned = super().clean()
        q, a = (cleaned.get("question") or "").lower(), (cleaned.get("answer") or "").lower()
        if q and a and len(a) >= 4 and a in q:
            raise ValidationError("The question must not contain the answer.")
        return cleaned


class BasePrivateDetailFormSet(forms.BaseFormSet):
    default_error_messages = {
        "too_few_forms": "Add at least one private detail so the real owner can prove the item is theirs.",
    }


PrivateDetailFormSet = forms.formset_factory(PrivateDetailForm, formset=BasePrivateDetailFormSet, extra=3, max_num=5,
                                             min_num=1, validate_min=True)


class ClaimForm(forms.Form):
    statement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}), max_length=1500, min_length=10,
        label="What does the item look like?",
        help_text="Colour, brand, size, marks, stickers, contents... in your own words. Only campus security sees this.")
    where_when = forms.CharField(max_length=300, min_length=3, label="Where and approximately when did you lose it?",
                                 help_text="For example: Library 2nd floor, Tuesday afternoon.")
    extra_info = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=1500, required=False,
                                 label="Any additional information? (optional)")
    attachment = forms.ImageField(required=False, label="Supporting photo (optional)",
                                  help_text="E.g. an old photo of you with the item or its receipt. JPEG, PNG or WebP. "
                                            "Only campus security can see it.")
    lost_report = forms.ModelChoiceField(queryset=Item.objects.none(), required=False,
                                         label="Link one of your lost reports (optional)")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lost_report"].queryset = Item.objects.lost().filter(reporter=user, status=Item.Status.OPEN)

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        return images.process_upload(f) if f else None


class RequestInfoForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=1500, min_length=5,
                              label="Question for the claimant",
                              help_text="Ask for evidence or detail. Never reveal the private details you are checking.")


class ClaimReplyForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), max_length=1500, min_length=3,
                              label="Your answer")


class VerificationForm(forms.Form):
    def __init__(self, *args, questions=(), **kwargs):
        super().__init__(*args, **kwargs)
        for q in questions:
            self.fields[str(q["id"])] = forms.CharField(
                label=q["question"], help_text=q["kind"], max_length=300,
                widget=forms.TextInput(attrs={"autocomplete": "off"}))


# --- Security -----------------------------------------------------------------

class CustodyPointField(forms.ModelChoiceField):
    def __init__(self, **kwargs):
        super().__init__(queryset=Location.objects.filter(is_custody_point=True, is_active=True), **kwargs)


class ReceiveItemForm(forms.Form):
    location = CustodyPointField(label="Custody point")
    received_from = forms.CharField(max_length=150, required=False, help_text="Leave blank if the finder brought it in.")
    notes = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 2}))


class TransferForm(forms.Form):
    location = CustodyPointField(label="Move to")
    notes = forms.CharField(max_length=500, required=False)


class CorrectionForm(forms.Form):
    record_id = forms.IntegerField(widget=forms.HiddenInput)
    notes = forms.CharField(max_length=500, label="What was wrong with this entry?")


class ApproveForm(forms.Form):
    reason = forms.CharField(max_length=500, required=False, label="Note for the claimant (optional)")
    confirm_competing = forms.BooleanField(required=False, label="I reviewed the competing claims; they will be rejected")
    confirm_compared = forms.BooleanField(
        required=True, label="I compared the claim with the finder's report and the private identifying details")


class RejectForm(forms.Form):
    reason = forms.CharField(max_length=500, widget=forms.Textarea(attrs={"rows": 2}),
                             label="Reason (shown to the claimant)")


class ScheduleHandoverForm(forms.Form):
    scheduled_for = forms.DateTimeField(widget=DATETIME_INPUT, input_formats=["%Y-%m-%dT%H:%M"])
    location = CustodyPointField(label="Collection point")
    officer = forms.ModelChoiceField(queryset=User.objects.none(), label="Supervising officer")
    method = forms.ChoiceField(choices=Handover.Method.choices, label="Confirmation method")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.db.models import Q
        self.fields["officer"].queryset = User.objects.filter(
            Q(groups__permissions__codename="conduct_handover") | Q(is_superuser=True), is_active=True).distinct()


class CompleteHandoverForm(forms.Form):
    id_type = forms.ChoiceField(choices=[("", "Select the ID you checked")] + list(Handover.IdType.choices), label="Photo ID checked")
    id_name_matches = forms.BooleanField(label="The name and photo on the ID match the claimant's account")
    otp_code = forms.CharField(max_length=6, required=False, label="Code shown on the claimant's phone",
                               widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off", "pattern": "[0-9]{6}"}))
    signature_data = forms.CharField(required=False, widget=forms.HiddenInput)
    proof_photo = forms.FileField(required=False, label="Proof photo (optional)",
                                  widget=forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"}))
    notes = forms.CharField(max_length=500, required=False)


class DisposalForm(forms.Form):
    method = forms.ChoiceField(choices=DisposalRecord.Method.choices)
    reason = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
    recipient = forms.CharField(max_length=150, required=False)
    evidence = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))


class ExtendRetentionForm(forms.Form):
    days = forms.IntegerField(min_value=1, max_value=365, initial=14)
    reason = forms.CharField(max_length=200)


# --- Administration -----------------------------------------------------------

class CategoryForm(forms.ModelForm):
    class Meta:
        model = ItemCategory
        fields = ["name", "parent", "description", "retention_days", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = ItemCategory.objects.filter(parent__isnull=True)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = qs
        self.fields["parent"].required = False


class AttributeForm(forms.ModelForm):
    class Meta:
        model = CategoryAttribute
        fields = ["category", "key", "label", "kind", "choices", "used_for_matching", "sort_order", "is_active"]


class LocationForm(forms.ModelForm):
    class Meta:
        model = Location
        fields = ["name", "kind", "parent", "code", "latitude", "longitude", "is_custody_point", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Location.objects.all()
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = qs


class RetentionSettingsForm(forms.ModelForm):
    class Meta:
        model = OperationalSettings
        fields = ["default_retention_days", "expiry_warning_days", "escalation_days", "hold_reports_for_review"]


class MatchingSettingsForm(forms.ModelForm):
    class Meta:
        model = OperationalSettings
        fields = ["match_threshold", "match_time_window_days", "weight_category", "weight_text", "weight_attributes",
                  "weight_location", "weight_time", "challenge_question_count", "verification_max_attempts",
                  "verification_pass_threshold", "otp_ttl_minutes", "otp_max_attempts"]


class UserRolesForm(forms.Form):
    roles = forms.MultipleChoiceField(choices=[(r, r) for r in roles.ROLE_PERMISSIONS], required=False,
                                      widget=forms.CheckboxSelectMultiple)
    is_active = forms.BooleanField(required=False, label="Account active")
    confirm_password = forms.CharField(
        required=False, label="Your password", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Required when granting Lost & Found team or administrator access.")


# --- Staff sign-in ---------------------------------------------------------------

class StaffLoginForm(RateLimitedLoginForm):
    """Same checks as the member sign-in, plus the account must hold a staff role."""
    STAFF_PERMS = ("access_security_dashboard", "review_reports", "manage_users", "manage_taxonomy", "view_analytics")

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not (user.is_superuser or any(user.has_perm(f"lostfound.{p}") for p in self.STAFF_PERMS)):
            raise ValidationError("This sign-in is for the Lost & Found team and administrators. "
                                  "Students and teachers should use their own sign-in.", code="not_staff")
        # Superusers (created on the server with manage.py) stay exempt so the system can't be locked out.
        if not user.is_superuser and not OperationalSettings.load().staff_email_allowed(user.email):
            raise ValidationError("Your email address isn't on the list allowed for staff accounts. "
                                  "Ask an administrator to update the sign-in policy.", code="staff_email_blocked")


# --- Report review -----------------------------------------------------------------

class ReportVerifyForm(forms.Form):
    note = forms.CharField(max_length=300, required=False, label="Note for the record (optional)")
    quality_bonus = forms.BooleanField(required=False, label="Exceptionally complete report (award the reviewer bonus)")


class ReportRejectForm(forms.Form):
    reason = forms.CharField(max_length=300, min_length=5, label="Reason (sent to the reporter)",
                             widget=forms.TextInput(attrs={"placeholder": "e.g. Duplicate of F-00012, or not a campus item"}))


# --- Profile & rewards -------------------------------------------------------------

class ProfileForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    department = forms.CharField(max_length=120, required=False)
    display_name = forms.CharField(max_length=40, required=False, label="Leaderboard nickname",
                                   help_text="Shown instead of your name on the leaderboard. Leave blank to use your first name and initial.")
    show_on_leaderboard = forms.BooleanField(required=False, label="Show me on the community leaderboard")
    two_step_enabled = forms.BooleanField(
        required=False, label="Two-step sign-in",
        help_text="After your password, we email a 6-digit code to your registered address.")

    def clean_display_name(self):
        name = self.cleaned_data["display_name"].strip()
        if name and "@" in name:
            raise ValidationError("Don't use an email address as your nickname.")
        if name and UserRewardProfile.objects.filter(display_name__iexact=name).exclude(user=self.initial.get("user")).exists():
            raise ValidationError("That nickname is already taken.")
        return name


class RewardPolicyForm(forms.ModelForm):
    class Meta:
        model = RewardPolicy
        fields = ["rewards_enabled", "points_found_report", "points_custody", "points_return", "points_quality_bonus",
                  "max_community_award", "leaderboard_enabled", "leaderboard_period_days"]


class BadgeForm(forms.ModelForm):
    class Meta:
        model = AchievementBadge
        fields = ["name", "description", "metric", "threshold", "style", "is_active"]


class CommunityAwardForm(forms.Form):
    username = forms.CharField(max_length=150, label="Member username")
    points = forms.IntegerField(min_value=1, max_value=1000)
    reason = forms.CharField(max_length=200, min_length=5,
                             widget=forms.TextInput(attrs={"placeholder": "e.g. Ran the lost-property awareness stall"}))

    def clean_username(self):
        try:
            return User.objects.get(username=self.cleaned_data["username"].strip(), is_active=True)
        except User.DoesNotExist:
            raise ValidationError("No active member with that username.")


class LedgerReverseForm(forms.Form):
    reason = forms.CharField(max_length=180, min_length=5, label="Reason for correction")


# --- Account management (administrators) ---------------------------------------------

class UserDetailsForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    member_type = forms.ChoiceField(choices=UserProfile.MemberType.choices, label="Member type")
    department = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, target=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = target

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        qs = User.objects.filter(email__iexact=email)
        if self.target is not None:
            qs = qs.exclude(pk=self.target.pk)
        if qs.exists():
            raise ValidationError("Another account already uses this email.")
        return email


class AdminCreateUserForm(UserDetailsForm):
    """Creates an inactive account with no usable password. The person activates it
    from a one-time link sent to their own email and chooses their own password."""
    username = forms.CharField(max_length=150, help_text="Letters, digits and @/./+/-/_ only.")
    roles = forms.MultipleChoiceField(choices=[(r, r) for r in roles.ROLE_PERMISSIONS], required=False,
                                      widget=forms.CheckboxSelectMultiple, initial=[roles.MEMBERS])
    confirm_password = forms.CharField(label="Your password (to confirm it's you)",
                                       widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor

    def clean_username(self):
        name = self.cleaned_data["username"].strip()
        from django.contrib.auth.validators import UnicodeUsernameValidator
        UnicodeUsernameValidator()(name)
        if User.objects.filter(username__iexact=name).exists():
            raise ValidationError("That username is taken.")
        return name

    def clean_confirm_password(self):
        pw = self.cleaned_data["confirm_password"]
        if self.actor is None or not self.actor.check_password(pw):
            raise ValidationError("Your password is incorrect.")
        return pw

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("email"):
            try:
                check_staff_email(cleaned["email"], cleaned.get("roles", []))
            except ValidationError as exc:
                self.add_error("email", exc)
        return cleaned

    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(data["username"], data["email"], None,
                                        first_name=data["first_name"], last_name=data.get("last_name", ""))
        user.set_unusable_password()
        user.is_active = False
        user.save()
        UserProfile.objects.create(user=user, member_type=data["member_type"], department=data.get("department", ""))
        user.groups.set(Group.objects.filter(name__in=set(data["roles"]) | {roles.MEMBERS}))
        return user


# --- Student / teacher portals -----------------------------------------------------

class PortalLoginForm(RateLimitedLoginForm):
    """Sign-in for one portal: a student account can't use the teacher portal and vice versa."""
    portal = None

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        member_type = getattr(getattr(user, "profile", None), "member_type", None)
        if self.portal and member_type != self.portal:
            here = "student" if self.portal == UserProfile.MemberType.STUDENT else "teacher"
            other = "teacher" if here == "student" else "student"
            raise ValidationError(f"This is the {here} sign-in. Your account is registered as a "
                                  f"{other}; please use the {other} sign-in.", code="wrong_portal")


class AccountPasswordResetForm(PasswordResetForm):
    """Reset by username or email. The link only ever goes to the matching account's
    *registered* address (read from the database), never to the text that was typed."""
    email = forms.CharField(max_length=254, label="Username or email",
                            widget=forms.TextInput(attrs={"autocomplete": "username"}))

    def get_users(self, identifier):
        identifier = (identifier or "").strip()
        if not identifier:
            return []
        model = get_user_model()
        users = model._default_manager.filter(
            models_Q(username__iexact=identifier) | models_Q(email__iexact=identifier), is_active=True)
        return [u for u in users if u.has_usable_password() and u.email]

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        from django.template import loader

        from .services import mailer
        user = context["user"]
        # Defence in depth: always the stored address of the account, whatever was submitted.
        recipient = user.email
        subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
        body = loader.render_to_string(email_template_name, context)
        mailer.send(subject, body, recipient, purpose="password reset")


class StudentLoginForm(PortalLoginForm):
    portal = UserProfile.MemberType.STUDENT


class TeacherLoginForm(PortalLoginForm):
    portal = UserProfile.MemberType.STAFF


class StaffCodeForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6, label="6-digit code from your email",
                           widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code",
                                                         "pattern": "[0-9]{6}"}))

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise ValidationError("Enter the 6 digits from the email.")
        return code


STAFF_ROLES = (roles.SECURITY, roles.CAMPUS_ADMIN, roles.SYSTEM_ADMIN)


def check_staff_email(email, chosen_roles):
    """Team and administrator accounts are limited to the emails/domains in the sign-in policy."""
    if set(chosen_roles) & set(STAFF_ROLES) and not OperationalSettings.load().staff_email_allowed(email):
        raise ValidationError("Lost & Found team and administrator accounts must use an official campus email "
                              "address allowed by the sign-in policy.")


class AccessSettingsForm(forms.ModelForm):
    """Sign-in and account policy, edited by user managers from the admin portal."""
    confirm_password = forms.CharField(label="Your password (to confirm the change)",
                                       widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    class Meta:
        model = OperationalSettings
        fields = ["allow_student_registration", "allow_teacher_registration", "registration_email_domains",
                  "staff_email_domains", "staff_email_allowlist"]
        widgets = {"staff_email_allowlist": forms.Textarea(attrs={"rows": 3, "placeholder": "admin@campus.edu"})}

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor

    def clean_confirm_password(self):
        pw = self.cleaned_data["confirm_password"]
        if self.actor is None or not self.actor.check_password(pw):
            raise ValidationError("Your password is incorrect.")
        return pw

    def clean(self):
        cleaned = super().clean()
        probe = OperationalSettings(staff_email_domains=cleaned.get("staff_email_domains", ""),
                                    staff_email_allowlist=cleaned.get("staff_email_allowlist", ""))
        for raw in probe._split(probe.staff_email_allowlist):
            if "@" not in raw:
                self.add_error("staff_email_allowlist", f"“{raw}” isn't an email address.")
        if self.actor is not None and not self.actor.is_superuser and not probe.staff_email_allowed(self.actor.email):
            raise ValidationError("This policy would block your own email from staff sign-in. "
                                  "Add your address to the allowed list first.")
        return cleaned
