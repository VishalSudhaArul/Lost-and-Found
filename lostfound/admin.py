from django.contrib import admin

from .models import (AuditEntry, CategoryAttribute, Claim, CustodyRecord, DisposalRecord, Handover, Item, ItemCategory,
                     Location, MatchSuggestion, Notification, OperationalSettings, UserProfile, VerificationChallenge)


class AttributeInline(admin.TabularInline):
    model = CategoryAttribute
    extra = 0


@admin.register(ItemCategory)
class ItemCategoryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "retention_days", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("name",)
    inlines = [AttributeInline]


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("full_path", "kind", "code", "is_custody_point", "is_active")
    list_filter = ("kind", "is_custody_point", "is_active")
    search_fields = ("name", "code", "full_path")


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Public fields only. Private details are deliberately NOT registered in
    the admin: they are viewed through the audited security review screen."""
    list_display = ("reference", "title", "report_type", "status", "category", "location", "created_at")
    list_filter = ("report_type", "status", "category")
    search_fields = ("title", "description")
    readonly_fields = ("reporter", "status", "custody_since", "current_custody_location", "resolved_at", "search_text")
    exclude = ("disposal_reviewed_by",)

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustodyRecord)
class CustodyRecordAdmin(ReadOnlyAdmin):
    list_display = ("item", "action", "from_location", "to_location", "accepted_by", "created_at")
    list_filter = ("action",)


@admin.register(AuditEntry)
class AuditEntryAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "actor", "summary")
    list_filter = ("action",)
    search_fields = ("summary", "target_id")


@admin.register(Claim)
class ClaimAdmin(ReadOnlyAdmin):
    list_display = ("reference", "item", "claimant", "status", "verification_status", "created_at")
    list_filter = ("status", "verification_status")


@admin.register(Handover)
class HandoverAdmin(ReadOnlyAdmin):
    list_display = ("receipt_code", "claim", "status", "scheduled_for", "officer")
    exclude = ("otp_hash",)


@admin.register(DisposalRecord)
class DisposalRecordAdmin(ReadOnlyAdmin):
    list_display = ("item", "method", "authorized_by", "disposed_at")


@admin.register(MatchSuggestion)
class MatchSuggestionAdmin(ReadOnlyAdmin):
    list_display = ("lost_item", "found_item", "score", "status")


@admin.register(VerificationChallenge)
class VerificationChallengeAdmin(ReadOnlyAdmin):
    list_display = ("claim", "status", "attempts_used", "max_attempts", "best_score")


admin.site.register(OperationalSettings)
admin.site.register(UserProfile)
admin.site.register(Notification)
