from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Campus Lost & Found administration"
admin.site.site_title = "Lost & Found admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("lostfound.urls")),
]

if settings.DEBUG:
    # Development only; only processed public item photos live under MEDIA_ROOT.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = "lostfound.views.errors.bad_request"
handler403 = "lostfound.views.errors.permission_denied"
handler404 = "lostfound.views.errors.page_not_found"
handler500 = "lostfound.views.errors.server_error"
