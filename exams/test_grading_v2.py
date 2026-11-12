from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .ai import grade_open
from .grading import grade_attempt
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class SemanticGradingTests(SimpleTestCase):
    def test_correct_alternative_function_is_not_bound_to_generated_example(self):
        question = Question(
            exam=Exam(subject="Computer Science"), kind="open", points=1,
            stem="State one function of the control unit.",
            model_answer="It directs data from memory to the ALU.",
            rubric=[{"criterion": "Directs data from memory to the ALU", "points": 1}],
        )
        answer = "It coordinates instruction execution."
        with patch("exams.ai.structured_response", return_value={
            "criteria": [{"score": 1, "evidence": "coordinates instruction execution"}],
            "needs_review": False, "review_reason": "", "feedback": "", "suggestion": "",
        }) as ai:
            grade = grade_open(question, answer)
        self.assertEqual((grade["score"], grade["criteria"]), (1, [1]))
        self.assertEqual(grade["grading_version"], "semantic-v2")
        instructions = ai.call_args.args[2].lower()
        self.assertIn("alternative", instructions)
        self.assertNotIn("using only the predefined marking criteria", instructions)


class FixedSemanticGradingTests(TestCase):
    def test_valid_partial_grade_with_rubric_gap_finishes_and_opens_review(self):
        teacher = get_user_model().objects.create(username="teacher-v2")
        user = get_user_model().objects.create(username="student-v2")
        student = Student.objects.create(user=user, name="Student", grade=9)
        exam = Exam.objects.create(
            title="Functions", subject="Computer Science", grade=9,
            mode=Exam.Mode.FIXED, question_count=1, total_points=2,
            published=True, created_by=teacher)
        invitation = Invitation.objects.create(
            exam=exam, student=student, code_hash="v2-grade",
            expires_at=timezone.now() + timedelta(hours=1), created_by=teacher)
        question = Question.objects.create(
            exam=exam, invitation=invitation, slot=1, kind="open", points=2,
            stem="State two functions of the control unit.",
            model_answer="Directs data to the ALU and controls timing.",
            rubric=[{"criterion": "Directs data to the ALU", "points": 1},
                    {"criterion": "Controls timing", "points": 1}])
        attempt = Attempt.objects.create(
            invitation=invitation, question_ids=[question.pk],
            answers={str(question.pk): "It coordinates instruction execution."},
            status=Attempt.Status.PENDING)
        reason = "The generated criterion omits another valid function."
        with patch("exams.ai.structured_response", return_value={
            "criteria": [{"score": 1, "evidence": "coordinates instruction execution"},
                         {"score": 0, "evidence": ""}],
            "needs_review": True, "review_reason": reason,
            "feedback": "", "suggestion": "",
        }):
            grade_attempt(attempt)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.score), (Attempt.Status.DONE, 1))
        grade = attempt.grades[str(question.pk)]
        self.assertEqual(grade["criteria"], [1, 0])
        self.assertTrue(grade["review_pending"])
        self.assertEqual(grade["grading_version"], "semantic-v2")
        appeal = GradeAppeal.objects.get(attempt=attempt, question=question)
        self.assertEqual(appeal.original_grade, grade)
        self.assertTrue(appeal.student_reason.startswith("Automatic grading sent this answer to the teacher."))
        self.assertIn(reason, appeal.student_reason)
