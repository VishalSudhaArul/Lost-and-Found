from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import roles
from .models import (CategoryAttribute, DisposalRecord, Handover, Item, ItemCategory, Location, OperationalSettings,
                     PrivateDetail, UserProfile)
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

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        domains = settings.ALLOWED_EMAIL_DOMAINS
        if domains and email.rsplit("@", 1)[-1] not in domains:
            raise ValidationError("Registration is limited to university email addresses.")
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        if commit:
            user.save()
            UserProfile.objects.create(user=user, member_type=self.cleaned_data["member_type"],
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
        widget=forms.Textarea(attrs={"rows": 4}), max_length=1500, min_length=20,
        label="Why is this yours?",
        help_text="Describe the item and when and where you lost it. Only campus security sees this.")
    lost_report = forms.ModelChoiceField(queryset=Item.objects.none(), required=False,
                                         label="Link one of your lost reports (optional)")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lost_report"].queryset = Item.objects.lost().filter(reporter=user, status=Item.Status.OPEN)


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
        fields = ["default_retention_days", "expiry_warning_days", "escalation_days"]


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
