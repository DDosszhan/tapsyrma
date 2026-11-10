from datetime import timedelta
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Attempt, Exam, Invitation, Question, Student


class AttemptImageAdminTests(TestCase):
    def test_staff_image_access_is_limited_to_the_requested_attempt(self):
        with TemporaryDirectory(dir=settings.BASE_DIR) as media_root, override_settings(MEDIA_ROOT=media_root):
            user_model = get_user_model()
            reviewer = user_model.objects.create_user("reviewer", is_staff=True)
            reviewer.user_permissions.add(Permission.objects.get(
                content_type__app_label="exams", codename="view_attempt"))
            student_user = user_model.objects.create_user("student")
            student = Student.objects.create(user=student_user, name="Student", grade=9,
                                             password_change_required=False)
            exam = Exam.objects.create(title="Exam", subject="Math", grade=9,
                                       question_count=1, total_points=1, created_by=reviewer)
            invitation = Invitation.objects.create(
                exam=exam, student=student, code_hash="image-test-code",
                expires_at=timezone.now() + timedelta(hours=1), created_by=reviewer)
            question = Question.objects.create(
                exam=exam, invitation=invitation, slot=1, kind=Question.Kind.OPEN,
                points=1, stem="What is shown?",
                image=SimpleUploadedFile("diagram.png", b"\x89PNG\r\n\x1a\n"))
            attempt = Attempt.objects.create(invitation=invitation, question_ids=[question.pk])
            other_invitation = Invitation.objects.create(
                exam=exam, student=student, code_hash="other-image-test-code",
                expires_at=timezone.now() + timedelta(hours=1), created_by=reviewer)
            fixed_question = Question.objects.create(
                exam=exam, slot=1, kind=Question.Kind.OPEN, points=1,
                stem="Fixed question", image=SimpleUploadedFile("fixed.png", b"\x89PNG\r\n\x1a\n"))
            other_attempt = Attempt.objects.create(invitation=other_invitation,
                                                   question_ids=[fixed_question.pk])
            url = reverse("exam_question_image", args=[question.pk])
            fixed_url = reverse("exam_question_image", args=[fixed_question.pk])

            self.client.force_login(reviewer)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get(f"{url}?attempt={other_attempt.pk}").status_code, 404)
            response = self.client.get(f"{url}?attempt={attempt.pk}")
            self.assertEqual(response.status_code, 200)
            response.close()
            self.assertEqual(self.client.get(f"{fixed_url}?attempt={attempt.pk}").status_code, 404)
            self.assertEqual(self.client.get(fixed_url).status_code, 404)
            response = self.client.get(f"{fixed_url}?attempt={other_attempt.pk}")
            self.assertEqual(response.status_code, 200)
            response.close()

            self.client.force_login(user_model.objects.create_user("other-staff", is_staff=True))
            self.assertEqual(self.client.get(f"{fixed_url}?attempt={other_attempt.pk}").status_code, 404)

            self.client.force_login(student_user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
