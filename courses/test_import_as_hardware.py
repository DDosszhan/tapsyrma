from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from courses.management.commands.import_as_hardware_lessons import COURSE_META, ROOT_TITLE, TOPICS
from courses.models import Course, Lesson, LessonQuestion, Section
from courses.templatetags.course_content import render_lesson_content


class ASHardwareImportTests(TestCase):
    def setUp(self):
        self.creator = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password"
        )
        self.grade_nine = Course.objects.create(
            title="Existing IGCSE", subject="Computer Science", grade=9, created_by=self.creator
        )

    def test_preview_import_rerun_and_separate_publication(self):
        preview = StringIO()
        call_command("import_as_hardware_lessons", creator_id=self.creator.pk, stdout=preview)
        self.assertIn("Would create draft course", preview.getvalue())
        self.assertIn("5 lesson(s) would be imported", preview.getvalue())
        self.assertEqual(Course.objects.filter(grade=10).count(), 0)
        with self.assertRaises(CommandError):
            call_command("import_as_hardware_lessons", creator_id=self.creator.pk,
                         apply=True, publish=True, stdout=StringIO())
        self.assertEqual(Course.objects.filter(grade=10).count(), 0)

        call_command("import_as_hardware_lessons", creator_id=self.creator.pk,
                     apply=True, stdout=StringIO())
        course = Course.objects.get(title=COURSE_META["title"])
        self.assertEqual((course.grade, course.subject, course.published), (10, "Computer Science", False))
        chapter = course.sections.get(parent__isnull=True, title=ROOT_TITLE)
        self.assertEqual(chapter.position, 3)
        self.assertEqual(list(chapter.subsections.values_list("position", "title")), list(enumerate(TOPICS, 1)))
        self.assertEqual(list(chapter.subsections.first().lessons.values_list("position", flat=True)), [1, 2, 3])
        self.assertEqual(list(chapter.subsections.last().lessons.values_list("position", flat=True)), [1, 2])
        self.assertEqual(Lesson.objects.filter(section__course=course).count(), 5)
        self.assertEqual(LessonQuestion.objects.filter(lesson__section__course=course).count(), 30)
        self.assertEqual(str(render_lesson_content(chapter.subsections.first().lessons.first().content)).count("<img"), 2)
        self.assertEqual(self.grade_nine.sections.count(), 0)

        rerun = StringIO()
        call_command("import_as_hardware_lessons", apply=True, stdout=rerun)
        self.assertIn("0 lesson(s) imported; 5 already imported; draft", rerun.getvalue())
        self.assertEqual(LessonQuestion.objects.filter(lesson__section__course=course).count(), 30)
        call_command("import_as_hardware_lessons", apply=True, publish=True, stdout=StringIO())
        course.refresh_from_db()
        self.assertTrue(course.published)
        self.assertEqual(Course.objects.filter(grade=10, published=True).count(), 1)

    def test_invalid_creator_and_conflicting_lesson_are_rejected(self):
        inactive = get_user_model().objects.create_superuser(
            "inactive@example.invalid", "inactive@example.invalid", "test-password",
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        with self.assertRaises(CommandError):
            call_command("import_as_hardware_lessons", creator_id=inactive.pk,
                         apply=True, stdout=StringIO())
        self.assertFalse(Course.objects.filter(grade=10).exists())

        call_command("import_as_hardware_lessons", creator_id=self.creator.pk,
                     apply=True, stdout=StringIO())
        lesson = Lesson.objects.get(title="Logic Gates and Truth Tables")
        lesson.content = "Teacher edit"
        lesson.save(update_fields=["content"])
        with self.assertRaises(CommandError):
            call_command("import_as_hardware_lessons", apply=True, publish=True, stdout=StringIO())
        self.assertFalse(Course.objects.get(title=COURSE_META["title"]).published)
        self.assertEqual(LessonQuestion.objects.filter(lesson__section__course__grade=10).count(), 30)
