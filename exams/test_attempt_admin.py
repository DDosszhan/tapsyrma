from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from .models import Attempt, Exam, Invitation, Question, Student


class AttemptAdminViewTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.invalid")
        self.student = Student.objects.create(name="Тестовый ученик", grade=9)
        exam = Exam.objects.create(
            title="Тестовый экзамен", subject="Информатика", grade=9,
            syllabus_text="Тема", question_count=6, total_points=7, created_by=self.admin,
        )
        invitation = Invitation.objects.create(
            exam=exam, student=self.student, code_hash="test-attempt-admin",
            expires_at=timezone.now() + timedelta(days=1), created_by=self.admin,
        )

        def question(slot, kind, stem, points=1, **kwargs):
            return Question.objects.create(
                exam=exam, invitation=invitation, slot=slot, kind=kind,
                stem=stem, points=points, **kwargs,
            )

        mcq = question(1, "mcq", "MCQ question?", options=["Wrong option", "Chosen option"])
        cloze = question(2, "cloze", "Fill [[1]] and [[2]].",
                         interaction={"answers": [["cache"], ["register"]]})
        matching = question(3, "matching", "Match the devices.",
                            options=["Output", "Input"],
                            interaction={"left": ["Keyboard", "Monitor"], "answers": [1, 0]})
        multiselect = question(4, "multiselect", "Choose devices.",
                               options=["Mouse", "Monitor", "Keyboard"],
                               interaction={"answers": [0, 2]})
        ordering = question(5, "ordering", "Order the steps.",
                            options=["Third", "First", "Second"],
                            interaction={"answers": [1, 2, 0]})
        open_question = question(6, "open", "Explain your answer.", points=2)
        self.attempt = Attempt.objects.create(
            invitation=invitation,
            question_ids=[open_question.pk, mcq.pk, cloze.pk, matching.pk,
                          multiselect.pk, ordering.pk],
            answers={
                str(open_question.pk): "A written explanation.",
                str(mcq.pk): "1",
                str(cloze.pk): ["cache", ""],
                str(matching.pk): ["1", "0"],
                str(multiselect.pk): ["0", "2"],
                str(ordering.pk): ["1", "2", "0"],
            },
            grades={str(open_question.pk): {
                "score": 1, "criteria": [1], "explanation": "Checked answer.",
            }},
            score=1, status=Attempt.Status.DONE,
        )
        self.url = f"/admin/exams/attempt/{self.attempt.pk}/change/"

    def test_review_shows_questions_with_readable_answers_and_grades(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()

        stems = ["Explain your answer.", "MCQ question?", "Fill [[1]] and [[2]].",
                 "Match the devices.", "Choose devices.", "Order the steps."]
        self.assertEqual([item["question"].stem for item in response.context["attempt_items"]], stems)
        self.assertEqual(sorted(page.index(stem) for stem in stems), [page.index(stem) for stem in stems])
        for value in ("A written explanation.", "Chosen option", "cache", "Keyboard",
                      "Input", "Mouse", "Keyboard", "First", "Second", "Third",
                      "1 / 2", "Checked answer."):
            self.assertIn(value, page)
        for field in ("field-question_ids", "field-answers", "field-grades"):
            self.assertNotIn(field, page)

    def test_staff_with_view_permission_can_review_attempt(self):
        staff = get_user_model().objects.create_user("reviewer", "reviewer@example.invalid", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="exams", codename="view_attempt"))
        self.client.force_login(staff)
        self.assertContains(self.client.get(self.url), "A written explanation.")
