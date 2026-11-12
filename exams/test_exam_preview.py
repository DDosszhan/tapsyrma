from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Attempt, Exam, Invitation, Question


class FixedExamPreviewTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "preview-admin", "preview@example.invalid", "password")
        self.other = get_user_model().objects.create_user("other", password="password")
        self.exam = Exam.objects.create(
            title="Preview exam", subject="Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=2, total_points=3, duration_minutes=60, published=False,
            created_by=self.admin)
        self.first = Question.objects.create(
            exam=self.exam, slot=1, kind="cloze", points=2,
            stem="A | B | X\n0 | 1 | [[1]]\n1 | 0 | [[2]]", interaction={"answers": [["A"], ["B"]]},
            rubric=[{"criterion": "First", "points": 1}, {"criterion": "Second", "points": 1}],
            model_answer="TOPSECRET")
        Question.objects.create(
            exam=self.exam, slot=2, kind="mcq", points=1, stem="Pick one.",
            options=["A", "B", "C", "D"], correct_option=2,
            rubric=[{"criterion": "Correct", "points": 1}], model_answer="TOPSECRET")

    def test_only_superuser_can_preview_draft_without_creating_an_attempt(self):
        url = f"/exam/preview/{self.exam.pk}/"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertContains(response, "Student preview")
        self.assertContains(response, f'name="q_{self.first.pk}_0"')
        self.assertContains(response, "question-stem--table")
        self.assertContains(response, "Preview only. Your answers stay on this page.")
        self.assertContains(response, "Time limit: 60 minutes (preview)")
        self.assertNotContains(response, '<form id="answers"')
        self.assertNotContains(response, "TOPSECRET")
        self.assertNotContains(response, "Submit exam")
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertFalse(Invitation.objects.exists())
        self.assertFalse(Attempt.objects.exists())
