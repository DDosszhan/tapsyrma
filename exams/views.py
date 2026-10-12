import hashlib
import hmac
import itertools
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Attempt, Invitation, Question


@login_required
def enter(request):
    if request.method == "POST":
        code = request.POST.get("code", "").strip().upper().replace("-", "")
        digest = hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()
        invitation = Invitation.objects.filter(code_hash=digest).select_related("exam", "student").first()
        if not invitation or invitation.student.user_id != request.user.pk or (
            invitation.expires_at < timezone.now() and not hasattr(invitation, "attempt")
        ):
            return render(request, "exams/enter.html", {"error": "Код недействителен или срок начала истек."})
        request.session["invitation_id"] = invitation.id
        return redirect("exam_session")
    return render(request, "exams/enter.html")


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


def _new_variant(exam):
    pools = [list(exam.questions.filter(slot=slot).values_list("id", flat=True)) for slot in range(1, exam.question_count + 1)]
    if any(not pool for pool in pools):
        raise ValueError("Вопросы еще не готовы.")
    used = {tuple(ids) for ids in Attempt.objects.filter(invitation__exam=exam).values_list("question_ids", flat=True)}
    for _ in range(200):
        pick = tuple(secrets.choice(pool) for pool in pools)
        if pick not in used:
            return list(pick)
    for pick in itertools.product(*pools):
        if pick not in used:
            return list(pick)
    raise ValueError("Варианты закончились. Сгенерируйте еще один раунд вопросов.")


@login_required
def session(request):
    invitation = _invitation(request)
    attempt = Attempt.objects.filter(invitation=invitation).first()
    if attempt:
        if attempt.status != Attempt.Status.ACTIVE:
            return redirect("exam_result")
        return render(request, "exams/attempt.html", {"attempt": attempt, "questions": _questions(attempt), "answers": attempt.answers})
    if request.method == "POST":
        if invitation.expires_at < timezone.now():
            return render(request, "exams/ready.html", {"invitation": invitation, "error": "Срок начала истек."})
        try:
            with transaction.atomic():
                invitation.exam.__class__.objects.select_for_update().get(pk=invitation.exam_id)
                attempt, _ = Attempt.objects.get_or_create(invitation=invitation, defaults={"question_ids": _new_variant(invitation.exam)})
        except ValueError as exc:
            return render(request, "exams/ready.html", {"invitation": invitation, "error": str(exc)})
        return redirect("exam_session")
    return render(request, "exams/ready.html", {"invitation": invitation})


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
    attempt = Attempt.objects.filter(invitation=invitation).first()
    if not attempt or attempt.status != Attempt.Status.ACTIVE:
        return HttpResponseBadRequest("Попытка закрыта.")
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
    items = []
    if attempt.status == Attempt.Status.DONE:
        for q in _questions(attempt):
            items.append({"question": q, "answer": attempt.answers.get(str(q.id), ""),
                          "grade": attempt.grades[str(q.id)],
                          "criteria": zip(q.rubric, attempt.grades[str(q.id)]["criteria"])})
    return render(request, "exams/result.html", {"attempt": attempt, "items": items, "exam": invitation.exam})


@login_required
def history(request):
    if not getattr(request.user, "student_profile", None):
        raise PermissionDenied
    attempts = Attempt.objects.filter(invitation__student__user=request.user).select_related(
        "invitation__exam").order_by("-started_at")
    return render(request, "exams/history.html", {"attempts": attempts})
