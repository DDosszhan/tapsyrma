from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from courses.models import Course, Lesson, LessonAnswer, LessonProgress, LessonQuestion, Section
from exams.models import Student

from courses.management.commands.import_hardware_lessons import ROOT_TITLE, SECTIONS


class HardwareImportTests(TestCase):
    def test_dry_run_apply_rerun_and_conflict(self):
        teacher = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password"
        )
        course = Course.objects.create(
            title="Existing IGCSE course", subject="Computer Science", grade=9, created_by=teacher
        )
        Section.objects.create(course=course, position=1, title="Existing chapter")
        section = Section.objects.create(course=course, position=2, title="3.1 Computer architecture")
        Lesson.objects.create(section=section, position=1, title="Existing lesson", content="Keep me")

        preview = StringIO()
        call_command("import_hardware_lessons", course_id=course.pk, stdout=preview)
        self.assertIn("9 lesson(s) would be imported", preview.getvalue())
        self.assertEqual(course.sections.count(), 2)
        self.assertEqual(Lesson.objects.count(), 1)

        call_command("import_hardware_lessons", course_id=course.pk, apply=True, stdout=StringIO())
        self.assertEqual(course.sections.count(), 6)
        self.assertEqual(Lesson.objects.count(), 10)
        self.assertEqual(LessonQuestion.objects.count(), 54)
        root = course.sections.get(title=ROOT_TITLE)
        self.assertIsNone(root.parent_id)
        self.assertEqual(root.position, 3)
        self.assertEqual(list(root.subsections.values_list("position", "title")), list(enumerate(SECTIONS, 1)))
        section.refresh_from_db()
        self.assertEqual(section.parent_id, root.pk)
        self.assertEqual(section.position, 1)
        self.assertEqual(list(section.lessons.order_by("position").values_list("position", flat=True)), [1, 2, 3, 4])

        rerun = StringIO()
        call_command("import_hardware_lessons", course_id=course.pk, apply=True, stdout=rerun)
        self.assertIn("0 lesson(s) imported; 9 already imported", rerun.getvalue())
        self.assertEqual(LessonQuestion.objects.count(), 54)

        imported = section.lessons.get(position=2)
        imported.content = "Conflicting edit"
        imported.save(update_fields=["content"])
        with self.assertRaises(CommandError):
            call_command("import_hardware_lessons", course_id=course.pk, apply=True, stdout=StringIO())

    def test_reparents_flat_topics_without_touching_student_history(self):
        teacher = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password"
        )
        course = Course.objects.create(
            title="Existing IGCSE course", subject="Computer Science", grade=9, created_by=teacher
        )
        Section.objects.create(course=course, position=1, title="Existing chapter")
        call_command("import_hardware_lessons", course_id=course.pk, apply=True, stdout=StringIO())
        root = course.sections.get(title=ROOT_TITLE)
        topics = list(root.subsections.order_by("position"))
        topic_ids = [topic.pk for topic in topics]
        lesson_ids = list(Lesson.objects.order_by("pk").values_list("pk", flat=True))
        question_ids = list(LessonQuestion.objects.order_by("pk").values_list("pk", flat=True))
        question = LessonQuestion.objects.order_by("pk").first()
        revision = question.revision
        user = get_user_model().objects.create_user("student@example.invalid", "student@example.invalid", "password")
        student = Student.objects.create(user=user, name="Test student", grade=9, password_change_required=False)
        progress = LessonProgress.objects.create(
            student=student, lesson=question.lesson, completed_at=timezone.now()
        )
        answer = LessonAnswer.objects.create(
            student=student, question=question, question_revision=revision, answer="1",
            status=LessonAnswer.Status.CORRECT, score=question.points,
        )

        root.position = 6
        root.save(update_fields=["position"])
        for position, topic in enumerate(topics, 2):
            topic.parent = None
            topic.position = position
            topic.full_clean()
            topic.save(update_fields=["parent", "position"])
        root.delete()

        preview = StringIO()
        call_command("import_hardware_lessons", course_id=course.pk, stdout=preview)
        self.assertIn("4 existing topic(s) would move", preview.getvalue())
        self.assertEqual(course.sections.count(), 5)

        applied = StringIO()
        call_command("import_hardware_lessons", course_id=course.pk, apply=True, stdout=applied)
        self.assertIn("4 existing topic(s) moved", applied.getvalue())
        root = course.sections.get(title=ROOT_TITLE)
        self.assertEqual(root.position, 3)
        self.assertEqual(list(root.subsections.order_by("position").values_list("pk", flat=True)), topic_ids)
        self.assertEqual(list(Lesson.objects.order_by("pk").values_list("pk", flat=True)), lesson_ids)
        self.assertEqual(list(LessonQuestion.objects.order_by("pk").values_list("pk", flat=True)), question_ids)
        self.assertEqual(LessonQuestion.objects.get(pk=question.pk).revision, revision)
        self.assertIsNotNone(LessonProgress.objects.get(pk=progress.pk).completed_at)
        self.assertEqual(LessonAnswer.objects.get(pk=answer.pk).question_id, question.pk)
