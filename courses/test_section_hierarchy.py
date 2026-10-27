from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from courses.admin import SectionInline
from courses.models import Course, Section


class SectionHierarchyTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password"
        )
        self.course = Course.objects.create(
            title="IGCSE", subject="Computer Science", grade=9, created_by=self.teacher
        )
        self.chapter = Section.objects.create(course=self.course, position=1, title="Chapter 1")
        self.hardware = Section.objects.create(course=self.course, position=3, title="Chapter 3")
        self.topic = Section.objects.create(
            course=self.course, parent=self.hardware, position=1, title="3.1 Architecture"
        )

    def test_positions_are_unique_within_each_level(self):
        other_topic = Section(course=self.course, parent=self.chapter, position=1, title="1.1")
        other_topic.full_clean()
        other_topic.save()
        self.assertEqual(self.course.sections.filter(parent__isnull=True).count(), 2)
        with self.assertRaises(ValidationError):
            Section(course=self.course, position=3, title="Duplicate chapter").full_clean()
        with self.assertRaises(ValidationError):
            Section(course=self.course, parent=self.hardware, position=1, title="Duplicate topic").full_clean()

    def test_parent_must_be_in_same_course_and_only_one_level_deep(self):
        other_course = Course.objects.create(
            title="Other", subject="Computer Science", grade=9, created_by=self.teacher
        )
        with self.assertRaises(ValidationError):
            Section(course=other_course, parent=self.hardware, position=1, title="Wrong course").full_clean()
        with self.assertRaises(ValidationError):
            Section(course=self.course, parent=self.topic, position=1, title="Too deep").full_clean()
        self.hardware.parent = self.hardware
        with self.assertRaises(ValidationError):
            self.hardware.full_clean()

    def test_course_admin_inline_shows_only_chapters(self):
        request = RequestFactory().get("/admin/courses/course/")
        request.user = self.teacher
        inline = SectionInline(Course, admin.site)
        self.assertEqual(set(inline.get_queryset(request)), {self.chapter, self.hardware})
