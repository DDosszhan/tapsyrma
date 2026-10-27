from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from courses.models import Course, Lesson, LessonQuestion, Section


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
        self.assertEqual(course.sections.count(), 5)
        self.assertEqual(Lesson.objects.count(), 10)
        self.assertEqual(LessonQuestion.objects.count(), 54)
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
