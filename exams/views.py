import hashlib
import hmac
import mimetypes
import secrets
from types import SimpleNamespace
from uuid import UUID
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from courses.models import Course

from .models import Attempt, AttemptVisibilityEvent, Exam, GradeAppeal, Invitation, Question, SharedCode
from .grading import AUTO_REVIEW_REASON


EXAM_CLOSED_MESSAGE = "This exam is not open for new attempts."


def _running_attempts():
    return Attempt.objects.filter(
        Q(deadline_at__gt=timezone.now()) | Q(deadline_at__isnull=True),
        status=Attempt.Status.ACTIVE,
    )


def _resumable_attempts(user):
    return _running_attempts().filter(invitation__student__user=user)


def _latest_work(student):
    work = (Attempt.objects.filter(invitation__student=student).exclude(status=Attempt.Status.ACTIVE)
            .select_related("invitation__exam")
            .only("status", "score", "submitted_at", "started_at", "invitation__exam__title",
                  "invitation__exam__total_points")
            .order_by(Coalesce("submitted_at", "started_at").desc(), "-pk").first())
    if work and work.status == Attempt.Status.DONE:
        # Automatic referrals and appeals share a table. Only show "Appeal pending"
        # for a student request or a student note added to a referral.
        work.appeal_count = work.pending_appeal_count = work.pending_review_count = 0
        for status, reason, note, referred in work.appeals.values_list(
                "status", "student_reason", "student_note", "original_grade__review_pending"):
            is_appeal = bool(note) or not (referred or reason.startswith(AUTO_REVIEW_REASON))
            work.appeal_count += int(is_appeal)
            if status == GradeAppeal.Status.PENDING:
                work.pending_review_count += 1
                work.pending_appeal_count += int(is_appeal)
    return work


def _render_home(request, error=None):
    student = getattr(request.user, "student_profile", None)
    exams = Exam.objects.filter(published=True).only(
        "title", "subject", "grade", "question_count", "duration_minutes", "total_points"
    ).order_by("grade", "subject", "title")
    context = {"student": student, "error": error, "exams": exams}
    if student:
        context.update(
            exams=exams.filter(grade=student.grade),
            active_attempts=_resumable_attempts(request.user).select_related("invitation__exam").only(
                "started_at", "deadline_at", "invitation__exam__title").order_by("-started_at", "-pk"),
            courses=Course.for_student_with_progress(student),
            latest_work=_latest_work(student),
        )
    return render(request, "exams/enter.html", context)


def _select_invitation(request, invitation):
    if not invitation.exam.published and not hasattr(invitation, "attempt"):
        return _render_home(request, EXAM_CLOSED_MESSAGE)
    request.session["invitation_id"] = invitation.pk
    return redirect("exam_session")


def enter(request):
    if request.method == "POST":
        if not request.user.is_authenticated:
            return redirect_to_login(request.path)
        student = getattr(request.user, "student_profile", None)
        if not student:
            return _render_home(request, "Shared codes are for students only. Sign in with a student account.")
        code = request.POST.get("code", "").strip().upper().replace("-", "")
        digest = hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()
        invitation = Invitation.objects.filter(code_hash=digest).select_related("exam", "student").first()
        if invitation and invitation.student.user_id == request.user.pk and (
            invitation.expires_at >= timezone.now() or hasattr(invitation, "attempt")
        ):
            return _select_invitation(request, invitation)
        shared = SharedCode.objects.filter(code_hash=digest).first()
        if shared:
            chosen = Invitation.objects.filter(shared_code=shared, student=student).first()
            if chosen:
                return _select_invitation(request, chosen)
            if shared.is_active and shared.expires_at >= timezone.now():
                request.session["shared_code_id"] = shared.id
                return redirect("choose_exam")
        return _render_home(request, "The code is invalid or its start deadline has passed.")
    return _render_home(request)


@login_required
@require_POST
def resume(request, attempt_id):
    attempt = get_object_or_404(_resumable_attempts(request.user).only("invitation_id"), pk=attempt_id)
    request.session["invitation_id"] = attempt.invitation_id
    return redirect("exam_attempt", attempt_id=attempt.pk)


@login_required
def choose_exam(request):
    student = getattr(request.user, "student_profile", None)
    if not student:
        raise PermissionDenied
    shared = get_object_or_404(SharedCode, pk=request.session.get("shared_code_id"))
    chosen = Invitation.objects.filter(shared_code=shared, student=student).first()
    if chosen:
        return _select_invitation(request, chosen)
    exams = Exam.objects.filter(published=True, grade=student.grade).order_by("subject", "title")
    if not shared.is_active or shared.expires_at < timezone.now():
        return render(request, "exams/choose.html", {"error": "The code has expired.", "exams": []})
    if request.method == "POST":
        exam_id = request.POST.get("exam_id", "")
        if not exam_id.isdecimal() or len(exam_id) > 20:
            return HttpResponseBadRequest("Invalid exam.")
        with transaction.atomic():
            locked = SharedCode.objects.select_for_update().get(pk=shared.pk)
            exam = Exam.objects.select_for_update().filter(pk=int(exam_id), published=True,
                                                           grade=student.grade).first()
            if not locked.is_active or locked.expires_at < timezone.now() or not exam:
                return render(request, "exams/choose.html", {"error": "This exam or code is no longer available.", "exams": exams})
            invitation, _ = Invitation.objects.get_or_create(
                shared_code=locked, student=student,
                defaults={"exam": exam, "code_hash": secrets.token_hex(32),
                          "expires_at": max(locked.expires_at, timezone.now() + timedelta(hours=1)),
                          "created_by": locked.created_by})
        request.session["invitation_id"] = invitation.id
        return redirect("exam_session")
    return render(request, "exams/choose.html", {"exams": exams})


def _invitation(request):
    invitation = Invitation.objects.filter(pk=request.session.get("invitation_id"),
                                           student__user=request.user).select_related("exam", "student").first()
    if not invitation:
        raise Http404
    return invitation


def _questions(attempt):
    by_id = Question.objects.in_bulk(attempt.question_ids)
    if len(by_id) != len(attempt.question_ids):
        raise Http404
    return [by_id[pk] for pk in attempt.question_ids]


@login_required
@require_GET
def preview(request, exam_id):
    if not request.user.is_superuser:
        raise PermissionDenied
    exam = get_object_or_404(Exam, pk=exam_id, mode=Exam.Mode.FIXED)
    questions = list(exam.questions.filter(invitation__isnull=True).order_by("slot"))
    student = SimpleNamespace(name="Student preview")
    attempt = SimpleNamespace(invitation=SimpleNamespace(exam=exam, student=student), deadline_at=None)
    return render(request, "exams/attempt.html", {
        "attempt": attempt, "questions": questions, "answers": {}, "preview": True,
    })


@login_required
def question_image(request, question_id):
    question = get_object_or_404(Question.objects.select_related("invitation__student"), pk=question_id)
    if not question.image:
        raise Http404
    if not request.user.is_superuser:
        allowed = False
        attempt_id = request.GET.get("attempt", "")
        if (request.user.is_staff and request.user.has_perm("exams.view_attempt")
                and attempt_id.isascii() and attempt_id.isdecimal() and len(attempt_id) <= 19
                and int(attempt_id) <= 9223372036854775807):
            attempt = Attempt.objects.filter(pk=int(attempt_id)).first()
            allowed = bool(attempt and question.pk in attempt.question_ids)
        if not allowed and question.invitation_id and question.invitation.student.user_id == request.user.pk:
            attempt = Attempt.objects.filter(invitation_id=question.invitation_id).first()
            allowed = bool(attempt and question.pk in attempt.question_ids)
        if not allowed:
            raise Http404
    try:
        stored_file = question.image.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404 from None
    content_type = mimetypes.guess_type(question.image.name)[0] or "application/octet-stream"
    response = FileResponse(stored_file, content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _close_expired(attempt):
    if attempt.status == Attempt.Status.ACTIVE and attempt.deadline_at and timezone.now() >= attempt.deadline_at:
        attempt.status = Attempt.Status.PENDING
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=["status", "submitted_at"])
        return True
    return False


def _valid_index(value, upper):
    return (value.isascii() and value.isdigit() and len(value) <= 3
            and str(int(value)) == value and int(value) < upper)


def _show_attempt(request, attempt, *, scoped):
    if attempt.status == Attempt.Status.ACTIVE and attempt.deadline_at and timezone.now() >= attempt.deadline_at:
        with transaction.atomic():
            attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
            _close_expired(attempt)
    if attempt.status != Attempt.Status.ACTIVE:
        if scoped:
            return redirect("exam_result_history", attempt_id=attempt.pk)
        return redirect("exam_result")
    remaining_seconds = max(0, int((attempt.deadline_at - timezone.now()).total_seconds())) if attempt.deadline_at else None
    return render(request, "exams/attempt.html", {"attempt": attempt, "questions": _questions(attempt),
                                                  "answers": attempt.answers, "remaining_seconds": remaining_seconds})


@login_required
@require_GET
def exam_attempt(request, attempt_id):
    attempt = get_object_or_404(Attempt, pk=attempt_id, invitation__student__user=request.user)
    return _show_attempt(request, attempt, scoped=True)


@login_required
def session(request):
    invitation = _invitation(request)
    attempt = Attempt.objects.filter(invitation=invitation).first()
    if attempt:
        return _show_attempt(request, attempt, scoped=False)
    if request.method == "POST":
        with transaction.atomic():
            # A student can have the start page open when the exam is unpublished.
            exam = Exam.objects.select_for_update().only("published", "question_count", "duration_minutes").get(
                pk=invitation.exam_id)
            locked = Invitation.objects.select_for_update().get(pk=invitation.pk)
            if not exam.published:
                if Attempt.objects.filter(invitation=locked).exists():
                    return redirect("exam_session")
                return _render_home(request, EXAM_CLOSED_MESSAGE)
            if locked.expires_at < timezone.now():
                return render(request, "exams/ready.html", {"invitation": locked, "expired": True})
            ids = list(locked.questions.order_by("slot").values_list("id", flat=True))
            if locked.variant_status != Invitation.VariantStatus.READY or len(ids) != exam.question_count:
                return render(request, "exams/ready.html", {"invitation": locked, "error": "Your question set is not ready yet."})
            Attempt.objects.get_or_create(
                invitation=locked,
                defaults={"question_ids": ids, "deadline_at": timezone.now() + timedelta(minutes=exam.duration_minutes)})
        return redirect("exam_session")
    if not invitation.exam.published:
        return _render_home(request, EXAM_CLOSED_MESSAGE)
    return render(request, "exams/ready.html", {"invitation": invitation,
                                                 "expired": invitation.expires_at < timezone.now()})


def _read_answers(request, questions, *, final=False):
    answers = {}
    for q in questions:
        if q.kind == "multiselect":
            if len(q.options) != 4 or f"q_{q.id}" in request.POST:
                raise ValueError("Invalid question configuration.")
            prefix = f"q_{q.id}_"
            if any(not _valid_index(key[len(prefix):], 4)
                   for key in request.POST if key.startswith(prefix)):
                raise ValueError("Invalid answer field.")
            selected = []
            for index in range(4):
                posted = request.POST.getlist(f"{prefix}{index}")
                if len(posted) > 1 or (posted and posted[0] != "1"):
                    raise ValueError("Invalid multiple-choice value.")
                if posted:
                    selected.append(str(index))
            if len(selected) > q.points:
                raise ValueError(f"Select no more than {q.points} options.")
            if (final and (q.interaction or {}).get("selection_rule") == "exact_correct_only"
                    and 0 < len(selected) < q.points):
                raise ValueError(f"Select exactly {q.points} options or leave the question unanswered.")
            answers[str(q.id)] = selected
            continue
        if q.kind in ("cloze", "matching", "ordering"):
            interaction = q.interaction or {}
            count = len(interaction.get("left", [])) if q.kind == "matching" else len(interaction.get("answers", []))
            if not 1 <= count <= 100 or f"q_{q.id}" in request.POST:
                raise ValueError("Invalid question configuration.")
            if q.kind == "ordering" and len(q.options) != count:
                raise ValueError("Invalid question configuration.")
            prefix = f"q_{q.id}_"
            if any(not _valid_index(key[len(prefix):], count)
                   for key in request.POST if key.startswith(prefix)):
                raise ValueError("Invalid answer field.")
            values = []
            for index in range(count):
                posted = request.POST.getlist(f"{prefix}{index}")
                if len(posted) > 1:
                    raise ValueError("Invalid answer field.")
                value = posted[0].strip() if posted else ""
                if q.kind == "cloze":
                    if len(value) > 120 or "\n" in value or "\r" in value:
                        raise ValueError("A blank answer is too long.")
                elif value and not _valid_index(value, len(q.options)):
                    raise ValueError("Invalid option.")
                values.append(value)
            if q.kind in ("matching", "ordering"):
                chosen = [value for value in values if value]
                if len(chosen) != len(set(chosen)):
                    raise ValueError("Use each option only once.")
            if sum(map(len, values)) > 5000:
                raise ValueError("Answer is too long (maximum 5,000 characters).")
            answers[str(q.id)] = values
            continue
        answer = request.POST.get(f"q_{q.id}", "").strip()
        if q.kind == "mcq":
            if answer not in ("", "0", "1", "2", "3"):
                raise ValueError("Invalid answer option.")
        elif len(answer) > 5000:
            raise ValueError("Answer is too long (maximum 5,000 characters).")
        answers[str(q.id)] = answer
    return answers


def _locked_page_attempt(request):
    """A student's open exam tabs may belong to different invitations."""
    attempts = Attempt.objects.select_for_update(of=("self",))
    attempt_id = request.POST.get("attempt_id")
    if attempt_id is None:  # Forms rendered before attempt IDs were included.
        attempt = attempts.filter(invitation=_invitation(request)).first()
        if attempt:
            question_ids = {key.split("_", 2)[1] for key in request.POST if key.startswith("q_")}
            if question_ids:
                if not question_ids.issubset(map(str, attempt.question_ids)):
                    raise Http404("Reopen the intended exam from Home.")
            elif _resumable_attempts(request.user).exclude(pk=attempt.pk).exists():
                raise Http404("Choose the intended exam from Home.")
        return attempt
    if not (attempt_id.isascii() and attempt_id.isdecimal() and len(attempt_id) <= 19):
        raise Http404
    return get_object_or_404(attempts, pk=int(attempt_id), invitation__student__user=request.user)


@login_required
@require_POST
def save(request):
    with transaction.atomic():
        attempt = _locked_page_attempt(request)
        if not attempt or attempt.status != Attempt.Status.ACTIVE:
            if attempt and "attempt_id" in request.POST:
                return JsonResponse({"error": "This attempt is closed."}, status=409)
            return HttpResponseBadRequest("This attempt is closed.")
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
def visibility(request):
    action = request.POST.get("action")
    if action not in ("hidden", "returned"):
        return HttpResponseBadRequest("Invalid event.")
    try:
        event_id = UUID(request.POST.get("event_id", ""))
    except (ValueError, AttributeError):
        return HttpResponseBadRequest("Invalid event ID.")
    with transaction.atomic():
        attempt = _locked_page_attempt(request)
        if not attempt:
            raise Http404
        _close_expired(attempt)
        event = AttemptVisibilityEvent.objects.filter(attempt=attempt, event_id=event_id).first()
        if event is None:
            if attempt.status != Attempt.Status.ACTIVE:
                return JsonResponse({"error": "This attempt is closed."}, status=409)
            event = AttemptVisibilityEvent.objects.create(attempt=attempt, event_id=event_id)
        if action == "returned" and event.returned_at is None:
            event.returned_at = timezone.now()
            event.save(update_fields=["returned_at"])
        count = AttemptVisibilityEvent.objects.filter(attempt=attempt).count()
    return JsonResponse({"count": count})


@login_required
@require_POST
def submit(request):
    with transaction.atomic():
        attempt = _locked_page_attempt(request)
        if attempt and attempt.status == Attempt.Status.ACTIVE and not _close_expired(attempt):
            try:
                answers = _read_answers(request, _questions(attempt), final=True)
            except ValueError as exc:
                return HttpResponseBadRequest(str(exc))
            attempt.answers = answers
            attempt.status = Attempt.Status.PENDING
            attempt.submitted_at = timezone.now()
            attempt.save(update_fields=["answers", "status", "submitted_at"])
    if "attempt_id" in request.POST:
        return redirect("exam_result_history", attempt_id=attempt.pk)
    return redirect("exam_result")


def _auto_review_pending(appeal, grade):
    return bool(appeal and appeal.status == GradeAppeal.Status.PENDING
                and ((grade or {}).get("review_pending")
                     or appeal.student_reason.startswith(AUTO_REVIEW_REASON)))


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
        if attempt_id is not None:
            return redirect("exam_attempt", attempt_id=attempt.pk)
        return redirect("exam_session")
    appeals = {appeal.question_id: appeal for appeal in attempt.appeals.all()}
    pending_review_count = sum(appeal.status == GradeAppeal.Status.PENDING for appeal in appeals.values())
    items = []
    for q in _questions(attempt):
        grade = attempt.grades.get(str(q.id))
        answer = attempt.answers.get(str(q.id), "")
        answer_rows = []
        correct_rows = []
        if q.kind in ("cloze", "matching", "multiselect", "ordering"):
            values = answer if isinstance(answer, list) else []
            if q.kind == "cloze":
                answer_key = grade.get("interaction_snapshot", q.interaction) if grade else q.interaction
                for index, alternatives in enumerate(answer_key.get("answers", [])):
                    answer_rows.append((f"Blank {index + 1}", values[index] if index < len(values) else ""))
                    correct_rows.append((f"Blank {index + 1}", ", ".join(alternatives)))
            elif q.kind == "matching":
                keys = q.interaction.get("answers", [])
                for index, left in enumerate(q.interaction.get("left", [])):
                    choice = values[index] if index < len(values) else ""
                    chosen = q.options[int(choice)] if isinstance(choice, str) and _valid_index(choice, len(q.options)) else ""
                    answer_rows.append((left, chosen))
                    correct = keys[index] if index < len(keys) else None
                    correct_rows.append((left, q.options[correct] if type(correct) is int and 0 <= correct < len(q.options) else ""))
            elif q.kind == "multiselect":
                selected = [q.options[int(value)] for value in values
                            if isinstance(value, str) and _valid_index(value, len(q.options))]
                answer_rows.append(("Selected options", ", ".join(selected)))
                correct = [q.options[index] for index in q.interaction.get("answers", [])
                           if type(index) is int and 0 <= index < len(q.options)]
                correct_rows.append(("Correct options", ", ".join(correct)))
            else:
                keys = q.interaction.get("answers", [])
                for index in range(len(q.options)):
                    choice = values[index] if index < len(values) else ""
                    chosen = q.options[int(choice)] if isinstance(choice, str) and _valid_index(choice, len(q.options)) else ""
                    answer_rows.append((f"Position {index + 1}", chosen))
                    correct = keys[index] if index < len(keys) else None
                    correct_rows.append((f"Position {index + 1}", q.options[correct] if type(correct) is int and 0 <= correct < len(q.options) else ""))
        question_appeal = appeals.get(q.id)
        auto_pending = _auto_review_pending(question_appeal, grade)
        if question_appeal and question_appeal.status == GradeAppeal.Status.PENDING:
            map_status, map_label = "review", "awaiting teacher"
        elif grade is None:
            map_status, map_label = "pending", "not graded yet"
        elif grade["score"] == q.points:
            map_status, map_label = "correct", "full marks"
        else:
            map_status, map_label = "wrong", "points lost"
        items.append({"question": q, "answer": answer, "answer_rows": answer_rows,
                      "correct_rows": correct_rows,
                      "grade": grade,
                      "display_rubric": grade.get("rubric_snapshot", q.rubric) if grade else q.rubric,
                      "display_model_answer": grade.get("model_answer_snapshot", q.model_answer) if grade else q.model_answer,
                      "criteria": zip(grade.get("rubric_snapshot", q.rubric), grade["criteria"]) if grade else [],
                      "appeal": question_appeal,
                      "map_status": map_status, "map_label": map_label,
                      "auto_pending": auto_pending,
                      "can_add_note": attempt.status == Attempt.Status.DONE and auto_pending
                                      and not question_appeal.student_note})
    exam = invitation.exam
    show_marking_details = exam.mode != Exam.Mode.FIXED or exam.answers_released
    # Rubrics and grading explanations can reveal the answers to a shared exam.
    show_grading_feedback = show_marking_details or (
        attempt.status == Attempt.Status.DONE and not exam.published
        and not _running_attempts().filter(invitation__exam=exam).exists()
    )
    return render(request, "exams/result.html", {
        "attempt": attempt,
        "items": items,
        "exam": exam,
        "show_marking_details": show_marking_details,
        "show_grading_feedback": show_grading_feedback,
        "visibility_count": attempt.visibility_events.count(),
        "pending_review_count": pending_review_count,
    })


@login_required
@require_POST
def appeal(request, attempt_id, question_id):
    reason = request.POST.get("reason", "").strip()
    if not 10 <= len(reason) <= 2000:
        return HttpResponseBadRequest("Describe the grading error (10 to 2,000 characters).")
    with transaction.atomic():
        attempt = get_object_or_404(Attempt.objects.select_for_update(), pk=attempt_id,
                                    invitation__student__user=request.user, status=Attempt.Status.DONE)
        question = get_object_or_404(Question, pk=question_id)
        if question_id not in attempt.question_ids or str(question_id) not in attempt.grades:
            raise Http404
        existing = GradeAppeal.objects.select_for_update().filter(attempt=attempt, question=question).first()
        if existing:
            if _auto_review_pending(existing, attempt.grades[str(question_id)]) and not existing.student_note:
                existing.student_note = reason
                existing.save(update_fields=["student_note"])
        else:
            GradeAppeal.objects.create(attempt=attempt, question=question,
                student_reason=reason, original_grade=attempt.grades[str(question_id)])
    return redirect("exam_result_history", attempt_id=attempt_id)


@login_required
def history(request):
    if not getattr(request.user, "student_profile", None):
        raise PermissionDenied
    attempts = Attempt.objects.filter(invitation__student__user=request.user).select_related(
        "invitation__exam").annotate(pending_review_count=Count(
            "appeals", filter=Q(appeals__status=GradeAppeal.Status.PENDING))).order_by("-started_at")
    return render(request, "exams/history.html", {"attempts": attempts})
