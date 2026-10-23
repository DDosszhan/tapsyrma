from django.db import transaction
from django.utils import timezone

from .ai import ManualReviewRequired, grade_open
from .models import Attempt, GradeAppeal, Question


AUTO_REVIEW_REASON = "Automatic grading sent this answer to the teacher."


def grade_attempt(attempt):
    questions = Question.objects.in_bulk(attempt.question_ids)
    grades = dict(attempt.grades)
    for pk in attempt.question_ids:
        if str(pk) in grades:
            continue
        question = questions[pk]
        answer = attempt.answers.get(str(pk), "")
        if question.kind == "mcq":
            earned = question.points if answer == str(question.correct_option) else 0
            grade = {"score": earned, "criteria": [earned],
                     "explanation": "Correct option." if earned else "The selected option is incorrect."}
        elif not answer:
            grade = {"score": 0, "criteria": [0] * len(question.rubric), "explanation": "No answer was given."}
        else:
            try:
                grade = grade_open(question, answer)
            except ManualReviewRequired as exc:
                grade = {"score": 0, "criteria": [0] * len(question.rubric),
                         "explanation": "Automatic grading could not be confirmed. The answer was sent to the teacher.",
                         "review_pending": True}
                review_reason = f"{AUTO_REVIEW_REASON} {exc.messages[0]}"[:500]
        # Save each result so a later API failure does not repeat paid grading calls.
        with transaction.atomic():
            locked = Attempt.objects.select_for_update().get(pk=attempt.pk)
            if str(pk) not in locked.grades:
                locked.grades = {**locked.grades, str(pk): grade}
                locked.score = sum(row["score"] for row in locked.grades.values())
                locked.save(update_fields=["grades", "score"])
                if grade.get("review_pending"):
                    GradeAppeal.objects.get_or_create(
                        attempt=locked, question=question,
                        defaults={"student_reason": review_reason,
                                  "original_grade": grade})
        grades[str(pk)] = grade
    with transaction.atomic():
        locked = Attempt.objects.select_for_update().get(pk=attempt.pk)
        if len(locked.grades) != len(attempt.question_ids):
            raise ValueError("Not all questions have been graded.")
        locked.status = Attempt.Status.DONE
        locked.graded_at = timezone.now()
        locked.error_message = ""
        locked.save(update_fields=["status", "graded_at", "error_message"])
    attempt.refresh_from_db()
