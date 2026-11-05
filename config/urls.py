from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve

admin.site.site_header = "Campus Lost & Found administration"
admin.site.site_title = "Lost & Found admin"

# The built-in admin has no separate sign-in: it goes through the two-step staff sign-in.
from lostfound.views.accounts import admin_site_login  # noqa: E402

admin.site.login = admin_site_login

def healthz(request):
    """Load-balancer health check: the app is up and PostgreSQL answers. Reveals nothing else."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("", include("lostfound.urls")),
]

if settings.SERVE_MEDIA:
    # Only processed, EXIF-stripped public item photos live under MEDIA_ROOT. Private files
    # (proof photos, signatures, claim attachments) are in PRIVATE_MEDIA_ROOT and are only
    # served by permission-checked views.
    urlpatterns += [re_path(r"^%s(?P<path>.*)$" % settings.MEDIA_URL.lstrip("/"), serve,
                            {"document_root": settings.MEDIA_ROOT})]

handler400 = "lostfound.views.errors.bad_request"
handler403 = "lostfound.views.errors.permission_denied"
handler404 = "lostfound.views.errors.page_not_found"
handler500 = "lostfound.views.errors.server_error"
