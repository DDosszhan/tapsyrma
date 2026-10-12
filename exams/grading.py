from django.core.exceptions import ValidationError
from django.utils import timezone

from .ai import grade_open
from .models import Attempt, Question


def grade_attempt(attempt):
    questions = Question.objects.in_bulk(attempt.question_ids)
    grades = {}
    total = 0
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
            grade = grade_open(question, answer)
        grades[str(pk)] = grade
        total += grade["score"]
    attempt.grades = grades
    attempt.score = total
    attempt.status = Attempt.Status.DONE
    attempt.graded_at = timezone.now()
    attempt.error_message = ""
    attempt.save(update_fields=["grades", "score", "status", "graded_at", "error_message"])
