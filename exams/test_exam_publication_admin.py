from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Attempt, Exam, ExamQuestionRule, Invitation, Question, Student


class ExamPublicationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = get_user_model().objects.create_superuser(
            "admin", "admin@example.invalid", "test-password")
        cls.student = Student.objects.create(name="Sample Learner", grade=9)

    def setUp(self):
        self.client.force_login(self.teacher)

    def issue_code(self, exam):
        return Invitation.objects.create(
            exam=exam, student=self.student, code_hash="sample-code-hash",
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)

    def test_hide_and_republish_a_used_fixed_exam_without_touching_its_data(self):
        exam = Exam.objects.create(title="Logic assessment", subject="Computing", grade=9,
            mode=Exam.Mode.FIXED, question_count=1, total_points=2, published=True, answers_released=True,
            created_by=self.teacher)
        question = Question.objects.create(exam=exam, slot=1, kind=Question.Kind.OPEN,
            grading_mode=Question.GradingMode.MANUAL, points=2, stem="Explain the result.",
            rubric=[{"criterion": "Correct explanation", "points": 2}])
        invitation = self.issue_code(exam)
        question_copy = Question.objects.create(exam=exam, invitation=invitation, slot=1, kind=question.kind,
            grading_mode=question.grading_mode, points=question.points, stem=question.stem, rubric=question.rubric)
        attempt = Attempt.objects.create(invitation=invitation, status=Attempt.Status.DONE, score=2,
            question_ids=[question_copy.pk], answers={str(question_copy.pk): "My answer."})
        url = reverse("admin:exams_exam_change", args=[exam.pk])
        form = {"title": exam.title, "duration_minutes": exam.duration_minutes, "answers_released": "on"}

        self.assertRedirects(self.client.post(url, form), reverse("admin:exams_exam_changelist"))
        exam.refresh_from_db()
        self.assertFalse(exam.published)
        self.assertTrue(exam.answers_released)
        self.assertContains(self.client.get(url), 'name="answers_released"')
        self.assertNotContains(self.client.get(url), 'name="questions-TOTAL_FORMS"')
        delete_url = reverse("admin:exams_question_delete", args=[question.pk])
        self.assertEqual(self.client.post(delete_url, {"post": "yes"}).status_code, 403)

        self.assertRedirects(self.client.post(url, {**form, "published": "on"}),
                             reverse("admin:exams_exam_changelist"))
        exam.refresh_from_db()
        self.assertTrue(exam.published)
        self.assertTrue(exam.answers_released)
        self.assertEqual((exam.question_count, exam.total_points), (1, 2))
        self.assertEqual(Question.objects.get(pk=question.pk).rubric, question.rubric)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.score, attempt.question_ids),
                         (Attempt.Status.DONE, 2, [question_copy.pk]))
        self.assertEqual(attempt.answers, {str(question_copy.pk): "My answer."})

    def test_hidden_exam_questions_remain_locked_after_a_code_is_issued(self):
        exam = Exam.objects.create(title="Set assessment", subject="Computing", grade=9,
            mode=Exam.Mode.FIXED, question_count=1, total_points=1, created_by=self.teacher)
        question = Question.objects.create(exam=exam, slot=1, kind=Question.Kind.OPEN,
                                           points=1, stem="Explain the result.")
        self.issue_code(exam)

        url = reverse("admin:exams_question_delete", args=[question.pk])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())

    def test_hide_generated_exam_keeps_the_existing_blueprint(self):
        exam = Exam.objects.create(title="Generated assessment", subject="Computing", grade=9,
            syllabus_text="Sample syllabus", question_count=2, total_points=2, published=True, created_by=self.teacher)
        rule = ExamQuestionRule.objects.create(exam=exam, kind=ExamQuestionRule.Kind.OPEN, count=2, points_each=1)
        self.issue_code(exam)
        url = reverse("admin:exams_exam_change", args=[exam.pk])
        form = {"title": exam.title, "duration_minutes": exam.duration_minutes}

        self.assertRedirects(self.client.post(url, form), reverse("admin:exams_exam_changelist"))
        exam.refresh_from_db()
        self.assertFalse(exam.published)
        self.assertRedirects(self.client.post(url, {**form, "published": "on"}),
                             reverse("admin:exams_exam_changelist"))
        exam.refresh_from_db()
        self.assertTrue(exam.published)
        self.assertEqual(list(exam.question_rules.values_list("pk", flat=True)), [rule.pk])
