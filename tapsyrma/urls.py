from pathlib import PurePosixPath

from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.urls import path
from django.views.generic import TemplateView


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


def private_media(request, path):
    if not request.user.is_superuser:
        raise PermissionDenied
    parts = PurePosixPath(path).parts
    if len(parts) < 2 or parts[0] != "syllabi" or ".." in parts:
        raise Http404
    try:
        stored_file = default_storage.open(path, "rb")
    except (FileNotFoundError, ValueError):
        raise Http404 from None
    return FileResponse(stored_file, as_attachment=True, filename=parts[-1])


urlpatterns = [
    path("", login_required(TemplateView.as_view(template_name="home.html")), name="home"),
    path("login/", LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("admin/private-media/<path:path>", admin.site.admin_view(private_media)),
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
]
