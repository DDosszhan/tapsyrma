"""Regrade selected attempts without hiding or overwriting their existing results."""
import time

from django.db import transaction
from django.utils import timezone

from .ai import ManualReviewRequired, grade_open
from .grading import AUTO_REVIEW_REASON, grade_structured
from .models import Attempt, Exam, GradeAppeal, Question, RegradeJob


def _decided_questions(attempt):
    return set(GradeAppeal.objects.filter(attempt=attempt).exclude(
        status=GradeAppeal.Status.PENDING).values_list("question_id", flat=True))


@transaction.atomic
def enqueue_regrade(attempt_id, requested_by):
    """Return a new job, or None when this selection has no eligible work."""
    attempt = Attempt.objects.select_for_update().get(pk=attempt_id)
    if attempt.status not in (Attempt.Status.DONE, Attempt.Status.ERROR):
        return None
    if RegradeJob.objects.filter(attempt=attempt, status__in=(
            RegradeJob.Status.PENDING, RegradeJob.Status.GRADING)).exists():
        return None
    if not attempt.question_ids:
        return None
    return RegradeJob.objects.create(
        attempt=attempt, requested_by=requested_by,
        original_status=attempt.status, original_grades=attempt.grades,
        original_score=attempt.score,
    )


def _fresh_grade(question, answer, exam_mode):
    if question.grading_mode == Question.GradingMode.MANUAL and answer:
        return {"score": 0, "criteria": [0] * len(question.rubric),
                "explanation": "This answer is awaiting teacher review.", "review_pending": True}
    if question.kind == Question.Kind.MCQ:
        earned = question.points if answer == str(question.correct_option) else 0
        return {"score": earned, "criteria": [earned],
                "explanation": "Correct option." if earned else "The selected option is incorrect."}
    if question.kind in (Question.Kind.CLOZE, Question.Kind.MATCHING,
                         Question.Kind.MULTISELECT, Question.Kind.ORDERING):
        return grade_structured(question, answer)
    if not answer:
        return {"score": 0, "criteria": [0] * len(question.rubric), "explanation": "No answer was given."}
    try:
        return grade_open(question, answer)
    except ManualReviewRequired as exc:
        if exam_mode == Exam.Mode.FIXED:
            raise
        return {"score": 0, "criteria": [0] * len(question.rubric),
                "explanation": "Automatic grading could not be confirmed. The answer was sent to the teacher.",
                "review_pending": True,
                "review_reason": f"{AUTO_REVIEW_REASON} {exc.messages[0]}"[:500]}


def _wait_for_live_grading():
    while Attempt.objects.filter(status__in=(Attempt.Status.PENDING, Attempt.Status.GRADING)).exists():
        time.sleep(2)


def _snapshot_matches(job, attempt):
    return (attempt.status == job.original_status and attempt.grades == job.original_grades
            and attempt.score == job.original_score)


def process_regrade_job(job):
    """Checkpoint new grades, then publish all of them in one transaction."""
    try:
        with transaction.atomic():
            locked = RegradeJob.objects.select_for_update().get(pk=job.pk)
            if locked.status not in (RegradeJob.Status.PENDING, RegradeJob.Status.GRADING):
                return False
            if locked.status == RegradeJob.Status.PENDING:
                locked.status = RegradeJob.Status.GRADING
                locked.started_at = timezone.now()
                locked.save(update_fields=["status", "started_at"])
        job.refresh_from_db()
        attempt = job.attempt
        if not _snapshot_matches(job, attempt):
            raise ValueError("The attempt or teacher decision changed during regrading.")
        questions = Question.objects.in_bulk(attempt.question_ids)
        candidate = dict(job.candidate_grades)
        decided_questions = _decided_questions(attempt)
        for pk in attempt.question_ids:
            key = str(pk)
            if key in candidate:
                continue
            question = questions[pk]
            answer = attempt.answers.get(key, "")
            old_grade = job.original_grades.get(key)
            if pk in decided_questions:
                if old_grade is None:
                    raise ValueError("A teacher-reviewed question has no saved grade.")
                grade = old_grade
            else:
                _wait_for_live_grading()
                grade = _fresh_grade(question, answer, attempt.invitation.exam.mode)
            candidate[key] = grade
            RegradeJob.objects.filter(pk=job.pk, status=RegradeJob.Status.GRADING).update(
                candidate_grades=candidate)
        with transaction.atomic():
            locked_attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
            locked_job = RegradeJob.objects.select_for_update().get(pk=job.pk)
            if not _snapshot_matches(locked_job, locked_attempt):
                raise ValueError("The attempt or teacher decision changed during regrading.")
            # A teacher may resolve an appeal while the AI is working.
            for pk in _decided_questions(locked_attempt):
                key = str(pk)
                if key not in locked_attempt.grades:
                    raise ValueError("A teacher-reviewed question has no saved grade.")
                candidate[key] = locked_attempt.grades[key]
            if set(candidate) != {str(pk) for pk in locked_attempt.question_ids}:
                raise ValueError("Not all questions have been regraded.")
            locked_attempt.grades = candidate
            locked_attempt.score = sum(grade["score"] for grade in candidate.values())
            locked_attempt.status = Attempt.Status.DONE
            locked_attempt.graded_at = timezone.now()
            locked_attempt.error_message = ""
            locked_attempt.save(update_fields=["grades", "score", "status", "graded_at", "error_message"])
            for pk in locked_attempt.question_ids:
                grade = candidate[str(pk)]
                if grade.get("review_pending"):
                    reason = grade.get("review_reason", "Manual grading requested for this question.")
                    if not reason.startswith(AUTO_REVIEW_REASON):
                        reason = f"{AUTO_REVIEW_REASON} {reason}"
                    GradeAppeal.objects.get_or_create(
                        attempt=locked_attempt, question=questions[pk],
                        defaults={"student_reason": reason[:500], "original_grade": grade})
            locked_job.status = RegradeJob.Status.DONE
            locked_job.candidate_grades = candidate
            locked_job.error_message = ""
            locked_job.finished_at = timezone.now()
            locked_job.save(update_fields=["status", "candidate_grades", "error_message", "finished_at"])
        return True
    except Exception as exc:
        RegradeJob.objects.filter(pk=job.pk, status__in=(
            RegradeJob.Status.PENDING, RegradeJob.Status.GRADING)).update(
            status=RegradeJob.Status.ERROR,
            error_message=f"{type(exc).__name__}: {exc}"[:500],
            finished_at=timezone.now())
        return False
