from django.contrib.auth import views as auth_views
from django.urls import path

from .views import accounts, administration, member, public, security

urlpatterns = [
    # Public
    path("", public.home, name="home"),
    path("board/", public.board, name="board"),
    path("board/<int:pk>/", public.item_detail, name="item_detail"),
    path("api/board/", public.board_json, name="board_json"),
    path("api/locations/", public.locations_json, name="locations_json"),

    # Accounts
    path("accounts/login/", accounts.SignInView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/register/", accounts.register, name="register"),
    path("accounts/password/", auth_views.PasswordChangeView.as_view(template_name="accounts/password_change.html"),
         name="password_change"),
    path("accounts/password/done/", auth_views.PasswordChangeDoneView.as_view(template_name="accounts/password_done.html"),
         name="password_change_done"),

    # Students & staff
    path("dashboard/", member.dashboard, name="dashboard"),
    path("report/<str:report_type>/", member.report_item, name="report_item"),
    path("my/reports/", member.my_reports, name="my_reports"),
    path("my/reports/<int:pk>/", member.report_detail, name="report_detail"),
    path("my/reports/<int:pk>/close/", member.close_report, name="close_report"),
    path("my/reports/<int:pk>/matches/", member.report_matches, name="report_matches"),
    path("items/<int:item_pk>/claim/", member.claim_create, name="claim_create"),
    path("my/claims/", member.my_claims, name="my_claims"),
    path("my/claims/<int:pk>/", member.claim_detail, name="claim_detail"),
    path("my/claims/<int:pk>/verify/", member.claim_verify, name="claim_verify"),
    path("my/claims/<int:pk>/withdraw/", member.claim_withdraw, name="claim_withdraw"),
    path("my/claims/<int:pk>/handover-code/", member.handover_code, name="handover_code"),
    path("notifications/", member.notifications_view, name="notifications"),
    path("notifications/<int:pk>/", member.notification_open, name="notification_open"),

    # Security
    path("security/", security.dashboard, name="security_dashboard"),
    path("security/items/", security.items, name="security_items"),
    path("security/items/<int:pk>/", security.item_detail, name="security_item"),
    path("security/items/<int:pk>/receive/", security.item_receive, name="security_item_receive"),
    path("security/items/<int:pk>/transfer/", security.item_transfer, name="security_item_transfer"),
    path("security/items/<int:pk>/correct/", security.custody_correct, name="security_custody_correct"),
    path("security/items/<int:pk>/private-detail/", security.private_detail_add, name="security_private_detail_add"),
    path("security/claims/", security.claims_queue, name="security_claims"),
    path("security/claims/<int:pk>/", security.claim_review, name="security_claim"),
    path("security/claims/<int:pk>/approve/", security.claim_approve, name="security_claim_approve"),
    path("security/claims/<int:pk>/reject/", security.claim_reject, name="security_claim_reject"),
    path("security/claims/<int:claim_pk>/schedule/", security.handover_schedule, name="security_handover_schedule"),
    path("security/handovers/", security.handovers, name="security_handovers"),
    path("security/handovers/<int:pk>/", security.handover_detail, name="security_handover"),
    path("security/handovers/<int:pk>/cancel/", security.handover_cancel, name="security_handover_cancel"),
    path("security/custody/", security.custody_history, name="security_custody"),
    path("security/ageing/", security.ageing, name="security_ageing"),
    path("security/ageing/<int:pk>/review/", security.ageing_review, name="security_ageing_review"),
    path("security/ageing/<int:pk>/extend/", security.ageing_extend, name="security_ageing_extend"),
    path("security/ageing/<int:pk>/dispose/", security.ageing_dispose, name="security_ageing_dispose"),
    path("security/files/<str:kind>/<int:pk>/", security.private_file, name="security_private_file"),

    # Administration
    path("manage/", administration.overview, name="admin_overview"),
    path("manage/analytics/", administration.analytics_view, name="admin_analytics"),
    path("manage/categories/", administration.categories, name="admin_categories"),
    path("manage/categories/<int:pk>/", administration.categories, name="admin_category_edit"),
    path("manage/locations/", administration.locations, name="admin_locations"),
    path("manage/locations/<int:pk>/", administration.locations, name="admin_location_edit"),
    path("manage/settings/", administration.settings_view, name="admin_settings"),
    path("manage/settings/run-matching/", administration.run_matching, name="admin_run_matching"),
    path("manage/users/", administration.users, name="admin_users"),
    path("manage/users/<int:pk>/", administration.user_edit, name="admin_user_edit"),
    path("manage/audit/", administration.audit_log, name="admin_audit"),
]
