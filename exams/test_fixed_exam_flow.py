from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .ai import grade_open
from .grading import grade_attempt
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class FixedExamFlowTests(TestCase):
    def setUp(self):
        media_dir = TemporaryDirectory(prefix=".test-media-", dir=settings.BASE_DIR)
        self.addCleanup(media_dir.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media_dir.name))
        self.teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        self.exam = Exam.objects.create(
            title="Logic gates", subject="Computer Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=2, total_points=4, published=True, created_by=self.teacher,
        )
        Question.objects.create(
            exam=self.exam, slot=1, kind="mcq", points=1, stem="Which gate?",
            options=["AND", "OR", "XOR", "NOT"], correct_option=0,
            rubric=[{"criterion": "Correct gate", "points": 1}], model_answer="AND",
            image=SimpleUploadedFile("gate.png", b"\x89PNG\r\n\x1a\n"),
        )
        Question.objects.create(
            exam=self.exam, slot=2, kind="open", grading_mode=Question.GradingMode.MANUAL,
            points=3, stem="Draw the circuit in words.",
            rubric=[{"criterion": "Circuit", "points": 3}],
        )
        self.invitations = []
        self.users = []
        for index in range(2):
            user = get_user_model().objects.create_user(f"student{index}@example.invalid", password="password")
            student = Student.objects.create(user=user, name=f"Student {index}", grade=10,
                                             password_change_required=False)
            invitation = Invitation.objects.create(
                exam=self.exam, student=student, code_hash=f"hash-{index}",
                expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher,
            )
            self.invitations.append(invitation)
            self.users.append(user)

    def test_every_student_gets_same_questions_without_generation_and_manual_review(self):
        with patch("exams.management.commands.prepare_invitations.generate_round") as generate:
            call_command("prepare_invitations", once=True)
            call_command("prepare_invitations", once=True)
            generate.assert_not_called()

        rows = [list(invitation.questions.order_by("slot")) for invitation in self.invitations]
        self.assertEqual([[q.stem for q in group] for group in rows],
                         [["Which gate?", "Draw the circuit in words."]] * 2)
        self.assertTrue(set(q.pk for q in rows[0]).isdisjoint(q.pk for q in rows[1]))
        self.assertEqual(rows[0][0].image.name, rows[1][0].image.name)
        self.assertTrue(all(Invitation.objects.get(pk=i.pk).variant_status == Invitation.VariantStatus.READY
                            for i in self.invitations))

        self.client.force_login(self.users[0])
        browser_session = self.client.session
        browser_session["invitation_id"] = self.invitations[0].pk
        browser_session.save()
        image_url = reverse("exam_question_image", args=[rows[0][0].pk])
        self.assertEqual(self.client.get(image_url).status_code, 404)
        self.assertEqual(self.client.post("/exam/").status_code, 302)
        image_response = self.client.get(image_url)
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(b"".join(image_response.streaming_content), b"\x89PNG\r\n\x1a\n")
        self.assertContains(self.client.get("/exam/"), image_url)
        self.assertEqual(self.client.get(reverse("exam_question_image", args=[rows[1][0].pk])).status_code, 404)

        answers = {f"q_{rows[0][0].pk}": "0", f"q_{rows[0][1].pk}": "AND then OR"}
        self.assertEqual(self.client.post("/exam/submit/", answers).status_code, 302)
        attempt = Attempt.objects.get(invitation=self.invitations[0])
        with patch("exams.grading.grade_open") as ai_grade:
            grade_attempt(attempt)
            ai_grade.assert_not_called()
        self.assertEqual(attempt.score, 1)
        self.assertTrue(GradeAppeal.objects.filter(attempt=attempt, question=rows[0][1]).exists())
        hidden_result = self.client.get("/exam/result/")
        self.assertContains(hidden_result, "Results and marking details will appear")
        self.assertNotContains(hidden_result, "Correct gate")
        self.assertNotContains(self.client.get("/history/"), "1 / 4.00 points")
        self.assertEqual(self.client.post(reverse("exam_appeal", args=[attempt.pk, rows[0][0].pk]),
                                          {"reason": "Please check this answer"}).status_code, 404)
        self.exam.answers_released = True
        self.exam.save(update_fields=["answers_released"])
        released_result = self.client.get("/exam/result/")
        self.assertContains(released_result, "Correct gate")
        self.assertContains(self.client.get("/history/"), "1 / 4.00 points")

    def test_open_answer_without_key_is_sent_to_ai_with_review_fallback(self):
        question = Question.objects.filter(exam=self.exam, slot=2, invitation__isnull=True).get()
        question.grading_mode = Question.GradingMode.AI
        with patch("exams.ai.structured_response", return_value={
            "criteria": [{"score": 2, "evidence": "AND then OR"}], "needs_review": False,
            "review_reason": "", "feedback": "", "suggestion": "",
        }) as response:
            grade = grade_open(question, "AND then OR")
        self.assertEqual(grade["score"], 2)
        self.assertIn("No model answer was supplied", response.call_args.args[2])
