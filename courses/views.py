from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .models import Course, LessonProgress, Section


def _student(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    return student


@login_required
@require_GET
def course_list(request):
    student = _student(request)
    courses = list(Course.objects.filter(published=True, grade=student.grade).annotate(
        section_count=Count("sections", distinct=True),
        lesson_count=Count("sections__lessons", distinct=True),
        opened_count=Count("sections__lessons__progress", filter=Q(
            sections__lessons__progress__student=student), distinct=True),
        completed_count=Count("sections__lessons__progress", filter=Q(
            sections__lessons__progress__student=student,
            sections__lessons__progress__completed_at__isnull=False), distinct=True),
    ))
    for course in courses:
        course.remaining_count = course.lesson_count - course.opened_count
    return render(request, "courses/list.html", {"courses": courses})


@login_required
@require_GET
def course_detail(request, course_id):
    student = _student(request)
    course = get_object_or_404(Course, pk=course_id, published=True, grade=student.grade)
    sections = list(course.sections.annotate(
        lesson_count=Count("lessons", distinct=True),
        opened_count=Count("lessons__progress", filter=Q(lessons__progress__student=student), distinct=True),
        completed_count=Count("lessons__progress", filter=Q(
            lessons__progress__student=student,
            lessons__progress__completed_at__isnull=False), distinct=True),
    ))
    for section in sections:
        section.remaining_count = section.lesson_count - section.opened_count
    return render(request, "courses/course.html", {"course": course, "sections": sections})


@login_required
@require_GET
def section_detail(request, course_id, section_id, lesson_id=None):
    student = _student(request)
    section = get_object_or_404(Section.objects.select_related("course"), pk=section_id,
        course_id=course_id, course__published=True, course__grade=student.grade)
    lessons = list(section.lessons.all())
    current = next((lesson for lesson in lessons if lesson.pk == lesson_id), None)
    if lesson_id is not None:
        if current is None:
            raise Http404
        LessonProgress.objects.get_or_create(student=student, lesson=current)
    progress = {row.lesson_id: row for row in LessonProgress.objects.filter(student=student, lesson__section=section)}
    for lesson in lessons:
        lesson.is_opened = lesson.pk in progress
        lesson.is_completed = lesson.is_opened and progress[lesson.pk].completed_at is not None
    opened_count = sum(lesson.is_opened for lesson in lessons)
    completed_count = sum(lesson.is_completed for lesson in lessons)
    return render(request, "courses/section.html", {"course": section.course, "section": section,
        "lessons": lessons, "current": current, "opened_count": opened_count,
        "completed_count": completed_count, "remaining_count": len(lessons) - opened_count})
