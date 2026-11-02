from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

admin.site.site_header = "DIMT Election Control"
admin.site.site_title = "DIMT Election Control"
admin.site.index_title = "Manage the election"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),         # secret admin address
    path("", include("voting.urls")),
    # Uploaded photos / backgrounds. For heavy traffic let nginx serve /media/ instead.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]

handler400 = "voting.views.error_400"
handler403 = "voting.views.error_403"
handler404 = "voting.views.error_404"
handler500 = "voting.views.error_500"
