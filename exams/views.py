import hashlib
import hmac
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Attempt, Exam, GradeAppeal, Invitation, Question, SharedCode


def enter(request):
    exams = Exam.objects.filter(published=True).order_by("grade", "subject", "title")
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.path)
        code = request.POST.get("code", "").strip().upper().replace("-", "")
        digest = hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()
        invitation = Invitation.objects.filter(code_hash=digest).select_related("exam", "student").first()
        if invitation and invitation.student.user_id == request.user.pk and (
            invitation.expires_at >= timezone.now() or hasattr(invitation, "attempt")
        ):
            request.session["invitation_id"] = invitation.id
            return redirect("exam_session")
        shared = SharedCode.objects.filter(code_hash=digest).first()
        student = getattr(request.user, "student_profile", None)
        if shared and student:
            chosen = Invitation.objects.filter(shared_code=shared, student=student).first()
            if chosen:
                request.session["invitation_id"] = chosen.id
                return redirect("exam_session")
            if shared.is_active and shared.expires_at >= timezone.now():
                request.session["shared_code_id"] = shared.id
                return redirect("choose_exam")
        return render(request, "exams/enter.html", {"error": "Код недействителен или срок начала истек.", "exams": exams})
    return render(request, "exams/enter.html", {"exams": exams})


@login_required
def choose_exam(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    shared = get_object_or_404(SharedCode, pk=request.session.get("shared_code_id"))
    chosen = Invitation.objects.filter(shared_code=shared, student=student).first()
    if chosen:
        request.session["invitation_id"] = chosen.id
        return redirect("exam_session")
    exams = Exam.objects.filter(published=True, grade=student.grade).order_by("subject", "title")
    if not shared.is_active or shared.expires_at < timezone.now():
        return render(request, "exams/choose.html", {"error": "Срок действия кода истек.", "exams": []})
    if request.method == "POST":
        exam_id = request.POST.get("exam_id", "")
        if not exam_id.isdecimal() or len(exam_id) > 20:
            return HttpResponseBadRequest("Неверный экзамен.")
        with transaction.atomic():
            locked = SharedCode.objects.select_for_update().get(pk=shared.pk)
            exam = Exam.objects.select_for_update().filter(pk=int(exam_id), published=True,
                                                           grade=student.grade).first()
            if not locked.is_active or locked.expires_at < timezone.now() or not exam:
                return render(request, "exams/choose.html", {"error": "Экзамен или код больше недоступен.", "exams": exams})
            invitation, _ = Invitation.objects.get_or_create(
                shared_code=locked, student=student,
                defaults={"exam": exam, "code_hash": secrets.token_hex(32),
                          "expires_at": max(locked.expires_at, timezone.now() + timedelta(hours=1)),
                          "created_by": locked.created_by})
        request.session["invitation_id"] = invitation.id
        return redirect("exam_session")
    return render(request, "exams/choose.html", {"exams": exams})


def _invitation(request):
    invitation = Invitation.objects.filter(pk=request.session.get("invitation_id"), exam__published=True,
                                           student__user=request.user).select_related("exam", "student").first()
    if not invitation:
        raise Http404
    return invitation


def _questions(attempt):
    by_id = Question.objects.in_bulk(attempt.question_ids)
    if len(by_id) != len(attempt.question_ids):
        raise Http404
    return [by_id[pk] for pk in attempt.question_ids]


def _close_expired(attempt):
    if attempt.status == Attempt.Status.ACTIVE and attempt.deadline_at and timezone.now() >= attempt.deadline_at:
        attempt.status = Attempt.Status.PENDING
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at"])
        return True
    return False


@login_required
def session(request):
    invitation = _invitation(request)
    attempt = Attempt.objects.filter(invitation=invitation).first()
    if attempt:
        if attempt.status == Attempt.Status.ACTIVE and attempt.deadline_at and timezone.now() >= attempt.deadline_at:
            with transaction.atomic():
                attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
                _close_expired(attempt)
        if attempt.status != Attempt.Status.ACTIVE:
            return redirect("exam_result")
        remaining_seconds = max(0, int((attempt.deadline_at - timezone.now()).total_seconds())) if attempt.deadline_at else None
        return render(request, "exams/attempt.html", {"attempt": attempt, "questions": _questions(attempt),
                                                       "answers": attempt.answers, "remaining_seconds": remaining_seconds})
    if request.method == "POST":
        with transaction.atomic():
            locked = Invitation.objects.select_for_update().get(pk=invitation.pk)
            if locked.expires_at < timezone.now():
                return render(request, "exams/ready.html", {"invitation": locked, "expired": True})
            ids = list(locked.questions.order_by("slot").values_list("id", flat=True))
            if locked.variant_status != Invitation.VariantStatus.READY or len(ids) != locked.exam.question_count:
                return render(request, "exams/ready.html", {"invitation": locked, "error": "Вариант еще не готов."})
            Attempt.objects.get_or_create(
                invitation=locked,
                defaults={"question_ids": ids, "deadline_at": timezone.now() + timedelta(minutes=locked.exam.duration_minutes)})
        return redirect("exam_session")
    return render(request, "exams/ready.html", {"invitation": invitation,
                                                 "expired": invitation.expires_at < timezone.now()})


def _read_answers(request, questions):
    answers = {}
    for q in questions:
        answer = request.POST.get(f"q_{q.id}", "").strip()
        if q.kind == "mcq":
            if answer not in ("", "0", "1", "2", "3"):
                raise ValueError("Недопустимый вариант ответа.")
        elif len(answer) > 5000:
            raise ValueError("Ответ слишком длинный (до 5000 символов).")
        answers[str(q.id)] = answer
    return answers


@login_required
@require_POST
def save(request):
    invitation = _invitation(request)
    with transaction.atomic():
        attempt = Attempt.objects.select_for_update().filter(invitation=invitation).first()
        if not attempt or attempt.status != Attempt.Status.ACTIVE:
            return HttpResponseBadRequest("Попытка закрыта.")
        if _close_expired(attempt):
            return JsonResponse({"expired": True}, status=409)
        try:
            answers = _read_answers(request, _questions(attempt))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        attempt.answers = answers
        attempt.save(update_fields=["answers"])
    return JsonResponse({"saved": True})


@login_required
@require_POST
def submit(request):
    invitation = _invitation(request)
    with transaction.atomic():
        attempt = Attempt.objects.select_for_update().filter(invitation=invitation).first()
        if not attempt or attempt.status != Attempt.Status.ACTIVE:
            return redirect("exam_result")
        if _close_expired(attempt):
            return redirect("exam_result")
        try:
            answers = _read_answers(request, _questions(attempt))
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        attempt.answers = answers
        attempt.status = Attempt.Status.PENDING
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["answers", "status", "submitted_at"])
    return redirect("exam_result")


@login_required
def result(request, attempt_id=None):
    if attempt_id is None:
        invitation = _invitation(request)
        attempt = Attempt.objects.filter(invitation=invitation).first()
    else:
        attempt = get_object_or_404(Attempt.objects.select_related("invitation__exam"),
                                    pk=attempt_id, invitation__student__user=request.user)
        invitation = attempt.invitation
    if not attempt:
        return redirect("exam_session")
    if attempt.status == Attempt.Status.ACTIVE:
        request.session["invitation_id"] = invitation.pk
        return redirect("exam_session")
    appeals = {appeal.question_id: appeal for appeal in attempt.appeals.all()}
    items = []
    for q in _questions(attempt):
        grade = attempt.grades.get(str(q.id))
        items.append({"question": q, "answer": attempt.answers.get(str(q.id), ""),
                      "grade": grade, "criteria": zip(q.rubric, grade["criteria"]) if grade else [],
                      "appeal": appeals.get(q.id)})
    return render(request, "exams/result.html", {"attempt": attempt, "items": items, "exam": invitation.exam})


@login_required
@require_POST
def appeal(request, attempt_id, question_id):
    reason = request.POST.get("reason", "").strip()
    if not 10 <= len(reason) <= 2000:
        return HttpResponseBadRequest("Опишите ошибку проверки (от 10 до 2000 символов).")
    with transaction.atomic():
        attempt = get_object_or_404(Attempt.objects.select_for_update(), pk=attempt_id,
                                    invitation__student__user=request.user, status=Attempt.Status.DONE)
        question = get_object_or_404(Question, pk=question_id)
        if question_id not in attempt.question_ids or str(question_id) not in attempt.grades:
            raise Http404
        GradeAppeal.objects.get_or_create(attempt=attempt, question=question,
            defaults={"student_reason": reason, "original_grade": attempt.grades[str(question_id)]})
    return redirect("exam_result_history", attempt_id=attempt_id)


@login_required
def history(request):
    if not getattr(request.user, "student_profile", None):
        raise PermissionDenied
    attempts = Attempt.objects.filter(invitation__student__user=request.user).select_related(
        "invitation__exam").order_by("-started_at")
    return render(request, "exams/history.html", {"attempts": attempts})
