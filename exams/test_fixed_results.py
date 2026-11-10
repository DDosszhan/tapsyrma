from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .ai import ManualReviewRequired
from .management.commands.grade_attempts import Command
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class FixedResultTests(TestCase):
    def setUp(self):
        teacher = get_user_model().objects.create_superuser(
            "teacher", "teacher@example.invalid", "password")
        user = get_user_model().objects.create_user("student@example.invalid", password="password")
        student = Student.objects.create(
            user=user, name="Student", grade=9, password_change_required=False)
        exam = Exam.objects.create(
            title="Fixed exam", subject="Science", grade=9, mode=Exam.Mode.FIXED,
            question_count=1, total_points=2, published=True, created_by=teacher)
        invitation = Invitation.objects.create(
            exam=exam, student=student, code_hash="fixed-results",
            expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
            variant_status=Invitation.VariantStatus.READY)
        question = Question.objects.create(
            exam=exam, invitation=invitation, slot=1, kind="open", points=2,
            stem="Explain the circuit.", rubric=[{"criterion": "SECRET_RUBRIC", "points": 2}],
            model_answer="SECRET_MODEL", subtopic_explanation="SECRET_TOPIC",
            answer_explanation="SECRET_ANSWER")
        self.exam = exam
        self.question = question
        self.invitation = invitation
        self.user = user

    def test_score_and_ticket_visible_while_marking_key_is_hidden(self):
        grade = {"score": 1, "criteria": [1], "explanation": "SECRET_GRADING"}
        attempt = Attempt.objects.create(
            invitation=self.invitation, question_ids=[self.question.pk],
            answers={str(self.question.pk): "My response"}, grades={str(self.question.pk): grade},
            score=1, status=Attempt.Status.DONE)
        GradeAppeal.objects.create(
            attempt=attempt, question=self.question, student_reason="Please check my score",
            original_grade=grade, status=GradeAppeal.Status.REJECTED,
            teacher_note="SECRET_TEACHER", reviewed_at=timezone.now())
        self.client.force_login(self.user)

        result = self.client.get(f"/exam/result/{attempt.pk}/")
        self.assertContains(result, "1 / 2.00 points")
        self.assertContains(result, "1 / 2")
        self.assertContains(result, "My response")
        for key in ("SECRET_RUBRIC", "SECRET_MODEL", "SECRET_TOPIC", "SECRET_ANSWER",
                    "SECRET_GRADING", "SECRET_TEACHER"):
            self.assertNotContains(result, key)
        self.assertContains(self.client.get("/history/"), "1 / 2.00 points")

        self.exam.answers_released = True
        self.exam.save(update_fields=["answers_released"])
        released = self.client.get(f"/exam/result/{attempt.pk}/")
        for key in ("SECRET_RUBRIC", "SECRET_MODEL", "SECRET_TOPIC", "SECRET_ANSWER",
                    "SECRET_GRADING", "SECRET_TEACHER"):
            self.assertContains(released, key)

    def test_uncertain_ai_grade_waits_for_retry_without_a_fake_zero_or_ticket(self):
        attempt = Attempt.objects.create(
            invitation=self.invitation, question_ids=[self.question.pk],
            answers={str(self.question.pk): "My response"}, status=Attempt.Status.PENDING,
            submitted_at=timezone.now())
        with patch("exams.grading.grade_open", side_effect=ManualReviewRequired("Uncertain")), \
                patch("exams.management.commands.grade_attempts.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                Command().handle()
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.ERROR)
        self.assertEqual(attempt.grades, {})
        self.assertIsNone(attempt.score)
        self.assertFalse(GradeAppeal.objects.filter(attempt=attempt).exists())
        self.client.force_login(self.user)
        failed_result = self.client.get(f"/exam/result/{attempt.pk}/")
        self.assertContains(failed_result, "Automatic grading failed")
        self.assertNotContains(failed_result, "My response")

        attempt.status = Attempt.Status.PENDING
        attempt.save(update_fields=["status"])
        with patch("exams.grading.grade_open", return_value={
            "score": 1, "criteria": [1], "explanation": "Checked"
        }), patch("exams.management.commands.grade_attempts.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                Command().handle()
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.score), (Attempt.Status.DONE, 1))
        self.assertFalse(GradeAppeal.objects.filter(attempt=attempt).exists())
        self.assertContains(self.client.get(f"/exam/result/{attempt.pk}/"), "1 / 2.00 points")
