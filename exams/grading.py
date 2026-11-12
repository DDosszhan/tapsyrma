from django.db import transaction
from django.utils import timezone

from .ai import ManualReviewRequired, grade_open
from .models import Attempt, Exam, GradeAppeal, Question


AUTO_REVIEW_REASON = "Automatic grading sent this answer to the teacher."


def grade_structured(question, answer):
    """Check objective multi-part answers against the stored question key."""
    if not isinstance(answer, list):
        raise ValueError("The saved answer does not match the question parts.")
    if question.kind == "multiselect":
        expected = question.interaction["answers"]
        if (len(question.rubric) != 1 or len(expected) != question.points
                or any(not isinstance(value, str) or not value.isascii() or not value.isdigit()
                       or str(int(value)) != value or int(value) >= len(question.options) for value in answer)
                or len(answer) != len(set(answer))):
            raise ValueError("The multiple-selection key or answer is invalid.")
        selected = {int(value) for value in answer}
        score = max(0, len(selected & set(expected)) - len(selected - set(expected)))
        return {"score": score, "criteria": [score],
                "explanation": f"{score} of {question.points} points for the selected options."}
    if len(answer) != len(question.rubric):
        raise ValueError("The saved answer does not match the question parts.")
    if question.kind == "cloze":
        expected = question.interaction["answers"]
        if len(expected) != len(answer):
            raise ValueError("The cloze key does not match the question parts.")
        normalize = lambda value: " ".join(value.casefold().split()).strip(".,;:!?")
        scores = [int(bool(value.strip()) and normalize(value) in {normalize(item) for item in choices})
                  for value, choices in zip(answer, expected)]
    elif question.kind in ("matching", "ordering"):
        expected = question.interaction["answers"]
        if len(expected) != len(answer):
            raise ValueError("The matching key does not match the question parts.")
        scores = [int(value == str(index)) for value, index in zip(answer, expected)]
    else:
        raise ValueError("Unsupported structured question type.")
    return {"score": sum(scores), "criteria": scores,
            "explanation": " ".join(f"Part {index}: {'correct' if score else 'incorrect'}."
                                   for index, score in enumerate(scores, 1))}


def grade_attempt(attempt):
    questions = Question.objects.in_bulk(attempt.question_ids)
    grades = dict(attempt.grades)
    for pk in attempt.question_ids:
        if str(pk) in grades:
            continue
        question = questions[pk]
        answer = attempt.answers.get(str(pk), "")
        review_reason = ""
        if question.grading_mode == Question.GradingMode.MANUAL and answer:
            grade = {"score": 0, "criteria": [0] * len(question.rubric),
                     "explanation": "This answer is awaiting teacher review.", "review_pending": True}
            review_reason = "Manual grading requested for this question."
        elif question.kind == "mcq":
            earned = question.points if answer == str(question.correct_option) else 0
            grade = {"score": earned, "criteria": [earned],
                     "explanation": "Correct option." if earned else "The selected option is incorrect."}
        elif question.kind in ("cloze", "matching", "multiselect", "ordering"):
            grade = grade_structured(question, answer)
        elif not answer:
            grade = {"score": 0, "criteria": [0] * len(question.rubric), "explanation": "No answer was given."}
        else:
            try:
                grade = grade_open(question, answer)
            except ManualReviewRequired as exc:
                if attempt.invitation.exam.mode == Exam.Mode.FIXED:
                    raise
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
                    if grade.get("review_reason"):
                        review_reason = f"{AUTO_REVIEW_REASON} {grade['review_reason']}"[:500]
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
