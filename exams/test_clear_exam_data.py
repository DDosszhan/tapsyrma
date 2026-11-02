from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from courses.models import Course, Lesson, LessonAnswer, LessonQuestion, Section
from .management.commands.clear_exam_data import EXAM_MODELS
from .models import (
    Attempt, AttemptVisibilityEvent, Exam, ExamQuestionRule, GradeAppeal,
    Invitation, Question, SharedCode, Student,
)


class ClearExamDataTests(TestCase):
    def test_dry_run_then_apply_removes_exams_only(self):
        media_dir = TemporaryDirectory(prefix=".test-media-", dir=settings.BASE_DIR)
        self.addCleanup(media_dir.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media_dir.name))
        user = get_user_model().objects.create_user("student@example.invalid", password="test-password")
        teacher = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password"
        )
        student = Student.objects.create(user=user, name="Student", grade=9)
        course = Course.objects.create(title="Course", subject="Math", grade=9, created_by=teacher)
        section = Section.objects.create(course=course, title="Section")
        lesson = Lesson.objects.create(section=section, title="Lesson")
        lesson_question = LessonQuestion.objects.create(
            lesson=lesson, prompt="2 + 2?", kind=LessonQuestion.Kind.MCQ,
            option_a="1", option_b="2", option_c="3", option_d="4", correct_option=4,
        )
        lesson_answer = LessonAnswer.objects.create(
            student=student, question=lesson_question, question_revision=lesson_question.revision,
            answer="4",
        )

        exam = Exam.objects.create(
            title="Old exam", subject="Math", grade=9, syllabus_text="Arithmetic",
            question_count=1, total_points=1, created_by=teacher,
        )
        exam.syllabus_file.save("old.txt", ContentFile(b"old exam material"))
        syllabus_name = exam.syllabus_file.name
        ExamQuestionRule.objects.create(exam=exam, kind="mcq", count=1, points_each=1)
        shared_code = SharedCode.objects.create(
            code_hash="a" * 64, expires_at=timezone.now() + timedelta(days=1), created_by=teacher,
        )
        invitation = Invitation.objects.create(
            exam=exam, student=student, shared_code=shared_code, code_hash="b" * 64,
            expires_at=timezone.now() + timedelta(days=1), created_by=teacher,
        )
        question = Question.objects.create(
            exam=exam, invitation=invitation, slot=1, kind="mcq", points=1,
            subtopic="Arithmetic", stem="2 + 2?", options=["1", "2", "3", "4"],
            correct_option=4, rubric=[], model_answer="4", subtopic_explanation="",
            answer_explanation="",
        )
        question.image.save("old.png", ContentFile(b"\x89PNG\r\n\x1a\n"))
        image_name = question.image.name
        attempt = Attempt.objects.create(invitation=invitation, question_ids=[question.pk])
        AttemptVisibilityEvent.objects.create(attempt=attempt, event_id=uuid.uuid4())
        GradeAppeal.objects.create(
            attempt=attempt, question=question, student_reason="Review", original_grade={},
        )

        output = StringIO()
        call_command("clear_exam_data", stdout=output)
        self.assertIn("Dry run", output.getvalue())
        self.assertTrue(all(model.objects.exists() for model in EXAM_MODELS))
        self.assertTrue(exam.syllabus_file.storage.exists(syllabus_name))
        self.assertTrue(question.image.storage.exists(image_name))

        call_command("clear_exam_data", "--apply", stdout=StringIO())
        self.assertTrue(all(not model.objects.exists() for model in EXAM_MODELS))
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertTrue(Student.objects.filter(pk=student.pk).exists())
        self.assertTrue(Course.objects.filter(pk=course.pk).exists())
        self.assertTrue(Section.objects.filter(pk=section.pk).exists())
        self.assertTrue(Lesson.objects.filter(pk=lesson.pk).exists())
        self.assertTrue(LessonQuestion.objects.filter(pk=lesson_question.pk).exists())
        self.assertTrue(LessonAnswer.objects.filter(pk=lesson_answer.pk).exists())
        self.assertFalse(exam.syllabus_file.storage.exists(syllabus_name))
        self.assertFalse(question.image.storage.exists(image_name))
