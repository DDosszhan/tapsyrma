from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Attempt, Exam, Invitation, Question, Student


class ExamTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("learner")
        cls.student = Student.objects.create(user=cls.user, grade=9, password_change_required=False)
        cls.exam = Exam.objects.create(title="Practice", grade=9, question_count=1, total_points=1,
                                      published=True, subject="Mathematics")

    def start(self, tab):
        invitation = Invitation.objects.create(exam=self.exam, student=self.student, code_hash=tab,
                                               expires_at=timezone.now() + timedelta(days=1), created_by=self.user)
        question = Question.objects.create(exam=self.exam, invitation=invitation, slot=1, points=1,
                                           kind=Question.Kind.OPEN, stem="Show your work.")
        return Attempt.objects.create(invitation=invitation, question_ids=[question.pk],
                                      answers={str(question.pk): tab})

    def select(self, attempt):
        session = self.client.session
        session["invitation_id"] = attempt.invitation_id
        session.save()

    def test_an_open_tab_stays_bound_to_its_attempt(self):
        first = self.start("first")
        second = self.start("second")
        self.client.force_login(self.user)
        self.select(first)
        page = self.client.get(reverse("exam_session"))
        result_url = reverse("exam_result_history", args=[first.pk])
        self.assertContains(page, f'name="attempt_id" value="{first.pk}"')
        self.assertContains(page, f'data-result-url="{result_url}"')

        self.select(second)
        answers = {"attempt_id": first.pk, f"q_{first.question_ids[0]}": "updated"}
        self.assertEqual(self.client.post(reverse("exam_save"), answers).status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.answers[str(first.question_ids[0])], "updated")
        event = {"attempt_id": first.pk, "action": "hidden", "event_id": uuid4()}
        self.assertEqual(self.client.post(reverse("exam_visibility"), event).json()["count"], 1)
        self.assertTrue(first.visibility_events.exists())
        self.assertRedirects(self.client.post(reverse("exam_submit"), answers), result_url)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Attempt.Status.PENDING)
        self.assertEqual(second.status, Attempt.Status.ACTIVE)
        self.assertEqual(second.answers, {str(second.question_ids[0]): "second"})
        self.assertFalse(second.visibility_events.exists())

    def test_timer_on_one_tab_preserves_the_other_selected_invitation(self):
        first = self.start("first")
        second = self.start("second")
        self.client.force_login(self.user)
        self.select(second)
        result_url = reverse("exam_result_history", args=[first.pk])
        self.assertRedirects(self.client.get(result_url), reverse("exam_attempt", args=[first.pk]))

        first.deadline_at = timezone.now() - timedelta(seconds=1)
        first.save(update_fields=["deadline_at"])
        response = self.client.get(result_url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], result_url)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, Attempt.Status.PENDING)
        self.assertEqual(second.status, Attempt.Status.ACTIVE)
        self.assertEqual(self.client.session["invitation_id"], second.invitation_id)

    def test_older_forms_cannot_overwrite_or_submit_a_different_selected_exam(self):
        first = self.start("first")
        second = self.start("second")
        self.client.force_login(self.user)
        self.select(second)
        for data in ({f"q_{first.question_ids[0]}": "older tab"}, {}):
            self.assertEqual(self.client.post(reverse("exam_save"), data).status_code, 404)
            self.assertEqual(self.client.post(reverse("exam_submit"), data).status_code, 404)
        second.refresh_from_db()
        self.assertEqual(second.answers, {str(second.question_ids[0]): "second"})
        self.assertEqual(second.status, Attempt.Status.ACTIVE)

    def test_writes_require_ownership_of_the_displayed_attempt(self):
        attempt = self.start("private")
        other_user = get_user_model().objects.create_user("other")
        self.client.force_login(other_user)
        data = {"attempt_id": attempt.pk, "action": "hidden", "event_id": uuid4()}
        for name in ("exam_save", "exam_visibility", "exam_submit"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name), data).status_code, 404)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.ACTIVE)
        self.assertEqual(attempt.answers[str(attempt.question_ids[0])], "private")
        self.assertFalse(attempt.visibility_events.exists())
