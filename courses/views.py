from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .jobs import check_answer
from .models import Course, LessonAnswer, LessonProgress, LessonQuestion, Section


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
    questions = list(current.questions.filter(active=True)) if current else []
    has_pending = False
    if questions:
        latest = {}
        for answer in (LessonAnswer.objects.filter(student=student, question__in=questions,
                                                   question_revision=F("question__revision"))
                       .order_by("-created_at", "-id")):
            latest.setdefault(answer.question_id, answer)
        for question in questions:
            question.latest_answer = latest.get(question.pk)
            answer = question.latest_answer
            scores = answer.criteria_scores if answer and answer.status in (
                LessonAnswer.Status.CORRECT, LessonAnswer.Status.INCORRECT) else []
            question.marking_rows_display = [
                {**row, "earned": scores[index] if index < len(scores) else None}
                for index, row in enumerate(question.student_marking_rows())
            ] if answer else []
            question.can_answer = not answer or answer.status not in (
                LessonAnswer.Status.CORRECT, LessonAnswer.Status.PENDING, LessonAnswer.Status.GRADING,
                LessonAnswer.Status.REVIEW, LessonAnswer.Status.ERROR)
            if answer and answer.explanation_status in (LessonAnswer.ExplanationStatus.PENDING,
                                                        LessonAnswer.ExplanationStatus.GENERATING):
                question.can_answer = False
            if answer:
                question.latest_answer_display = (question.options[int(answer.answer) - 1]
                                                  if question.kind == LessonQuestion.Kind.MCQ
                                                  and answer.answer in ("1", "2", "3", "4") else answer.answer)
            if answer and (answer.status in (LessonAnswer.Status.PENDING, LessonAnswer.Status.GRADING)
                           or answer.explanation_status in (LessonAnswer.ExplanationStatus.PENDING,
                                                            LessonAnswer.ExplanationStatus.GENERATING)):
                has_pending = True
    return render(request, "courses/section.html", {"course": section.course, "section": section,
        "lessons": lessons, "current": current, "opened_count": opened_count,
        "completed_count": completed_count, "remaining_count": len(lessons) - opened_count,
        "questions": questions, "has_pending": has_pending})


def _question_for_student(student, course_id, section_id, lesson_id, question_id):
    return get_object_or_404(LessonQuestion.objects.select_related("lesson__section__course"),
                             pk=question_id, lesson_id=lesson_id, lesson__section_id=section_id,
                             lesson__section__course_id=course_id, lesson__section__course__published=True,
                             lesson__section__course__grade=student.grade, active=True)


def _lesson_redirect(course_id, section_id, lesson_id, question_id):
    return redirect(reverse("lesson_detail", args=[course_id, section_id, lesson_id]) + f"#question-{question_id}")


@login_required
@require_POST
def submit_lesson_answer(request, course_id, section_id, lesson_id, question_id):
    student = _student(request)
    question = _question_for_student(student, course_id, section_id, lesson_id, question_id)
    answer_text = request.POST.get("answer", "").strip()
    limit = 2000 if question.kind == LessonQuestion.Kind.OPEN else 500
    if not answer_text or len(answer_text) > limit or (question.kind == LessonQuestion.Kind.MCQ
                                                       and answer_text not in ("1", "2", "3", "4")):
        messages.error(request, "Enter an answer of the allowed length and choose a valid option.")
        return _lesson_redirect(course_id, section_id, lesson_id, question_id)
    with transaction.atomic():
        question = LessonQuestion.objects.select_for_update().get(pk=question.pk)
        if not question.active:
            raise Http404
        if request.POST.get("revision") != str(question.revision):
            messages.error(request, "The question has changed. Reopen it and submit your answer again.")
            return _lesson_redirect(course_id, section_id, lesson_id, question_id)
        latest = (LessonAnswer.objects.filter(student=student, question=question,
                                              question_revision=question.revision).order_by("-created_at", "-id").first())
        if latest and (latest.status in (LessonAnswer.Status.CORRECT, LessonAnswer.Status.PENDING,
                                         LessonAnswer.Status.GRADING, LessonAnswer.Status.REVIEW,
                                         LessonAnswer.Status.ERROR)
                       or latest.explanation_status in (LessonAnswer.ExplanationStatus.PENDING,
                                                        LessonAnswer.ExplanationStatus.GENERATING)):
            return _lesson_redirect(course_id, section_id, lesson_id, question_id)
        LessonProgress.objects.get_or_create(student=student, lesson_id=lesson_id)
        answer = LessonAnswer.objects.create(student=student, question=question,
                                             question_revision=question.revision, answer=answer_text)
    if question.kind != LessonQuestion.Kind.OPEN:
        check_answer(answer)
    return _lesson_redirect(course_id, section_id, lesson_id, question_id)


@login_required
@require_POST
def request_lesson_explanation(request, course_id, section_id, lesson_id, question_id, answer_id):
    student = _student(request)
    question = _question_for_student(student, course_id, section_id, lesson_id, question_id)
    get_object_or_404(LessonAnswer, pk=answer_id, student=student, question=question)
    LessonAnswer.objects.filter(pk=answer_id, student=student, question=question,
                                question_revision=question.revision, status=LessonAnswer.Status.INCORRECT,
                                explanation_status__in=[LessonAnswer.ExplanationStatus.NONE,
                                                        LessonAnswer.ExplanationStatus.ERROR]).update(
        explanation_status=LessonAnswer.ExplanationStatus.PENDING, explanation="")
    return _lesson_redirect(course_id, section_id, lesson_id, question_id)
