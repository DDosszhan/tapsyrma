from django.db import transaction
from django.utils import timezone

from .ai import ManualReviewRequired, grade_open
from .models import Attempt, GradeAppeal, Question


def grade_attempt(attempt):
    questions = Question.objects.in_bulk(attempt.question_ids)
    grades = {}
    total = 0
    manual_review = []
    for pk in attempt.question_ids:
        question = questions[pk]
        answer = attempt.answers.get(str(pk), "")
        if question.kind == "mcq":
            earned = question.points if answer == str(question.correct_option) else 0
            grade = {"score": earned, "criteria": [earned],
                     "explanation": "Верный вариант." if earned else "Выбранный вариант неверен."}
        elif not answer:
            grade = {"score": 0, "criteria": [0] * len(question.rubric), "explanation": "Ответ не дан."}
        else:
            try:
                grade = grade_open(question, answer)
            except ManualReviewRequired:
                grade = {"score": 0, "criteria": [0] * len(question.rubric),
                         "explanation": "Автоматическая оценка не подтверждена. Ответ передан учителю."}
                manual_review.append((question, grade))
        grades[str(pk)] = grade
        total += grade["score"]
    with transaction.atomic():
        attempt.grades = grades
        attempt.score = total
        attempt.status = Attempt.Status.DONE
        attempt.graded_at = timezone.now()
        attempt.error_message = ""
        attempt.save(update_fields=["grades", "score", "status", "graded_at", "error_message"])
        for question, grade in manual_review:
            GradeAppeal.objects.get_or_create(
                attempt=attempt, question=question,
                defaults={"student_reason": "Автоматическая проверка передала ответ учителю.",
                          "original_grade": grade},
            )
