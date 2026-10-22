from pathlib import PurePosixPath

from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import connection
from django.http import FileResponse, Http404, JsonResponse
from django.urls import path
from django.views.generic import TemplateView
from exams import views as exam_views
from exams.accounts import StudentLoginView, StudentPasswordChangeView
from courses import views as course_views


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
    path("", exam_views.enter, name="enter"),
    path("courses/", course_views.course_list, name="course_list"),
    path("courses/<int:course_id>/", course_views.course_detail, name="course_detail"),
    path("courses/<int:course_id>/sections/<int:section_id>/", course_views.section_detail, name="section_detail"),
    path("courses/<int:course_id>/sections/<int:section_id>/lessons/<int:lesson_id>/",
         course_views.section_detail, name="lesson_detail"),
    path("courses/<int:course_id>/sections/<int:section_id>/lessons/<int:lesson_id>/questions/<int:question_id>/answer/",
         course_views.submit_lesson_answer, name="lesson_answer"),
    path("courses/<int:course_id>/sections/<int:section_id>/lessons/<int:lesson_id>/questions/<int:question_id>/answers/<int:answer_id>/explain/",
         course_views.request_lesson_explanation, name="lesson_explain"),
    path("choose/", exam_views.choose_exam, name="choose_exam"),
    path("exam/", exam_views.session, name="exam_session"),
    path("exam/save/", exam_views.save, name="exam_save"),
    path("exam/visibility/", exam_views.visibility, name="exam_visibility"),
    path("exam/submit/", exam_views.submit, name="exam_submit"),
    path("exam/result/", exam_views.result, name="exam_result"),
    path("exam/result/<int:attempt_id>/", exam_views.result, name="exam_result_history"),
    path("exam/result/<int:attempt_id>/appeal/<int:question_id>/", exam_views.appeal, name="exam_appeal"),
    path("history/", exam_views.history, name="history"),
    path("dashboard/", login_required(TemplateView.as_view(template_name="home.html")), name="home"),
    path("login/", StudentLoginView.as_view(), name="login"),
    path("password/change/", StudentPasswordChangeView.as_view(), name="password_change"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("admin/private-media/<path:path>", admin.site.admin_view(private_media)),
    path("admin/", admin.site.urls),
    path("healthz/", healthz, name="healthz"),
]
