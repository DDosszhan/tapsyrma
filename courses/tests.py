from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from exams.models import Student

from .models import Course, Lesson, LessonProgress, Section


class CourseSkeletonTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password")
        self.user = get_user_model().objects.create_user("nine@example.invalid", "nine@example.invalid", "password")
        self.student = Student.objects.create(user=self.user, name="Ученик 9", grade=9, password_change_required=False)
        self.other_user = get_user_model().objects.create_user("other@example.invalid", "other@example.invalid", "password")
        Student.objects.create(user=self.other_user, name="Другой ученик", grade=9, password_change_required=False)
        self.ten_user = get_user_model().objects.create_user("ten@example.invalid", "ten@example.invalid", "password")
        Student.objects.create(user=self.ten_user, name="Ученик 10", grade=10, password_change_required=False)
        self.course = Course.objects.create(title="Информатика", subject="Информатика", grade=9,
            description="Основы программирования", published=True, created_by=self.teacher)
        self.section = Section.objects.create(course=self.course, position=1, title="Алгоритмы")
        self.first = Lesson.objects.create(section=self.section, position=1, title="Переменные", content="Что такое переменная")
        self.second = Lesson.objects.create(section=self.section, position=2, title="Условия")
        self.draft = Course.objects.create(title="Черновик", subject="Математика", grade=9,
            created_by=self.teacher)
        self.ten_course = Course.objects.create(title="Курс 10", subject="Математика", grade=10,
            published=True, created_by=self.teacher)

    def test_courses_are_limited_to_student_grade_and_published_state(self):
        self.assertEqual(self.client.get(reverse("course_list")).status_code, 302)
        self.client.force_login(self.user)
        listing = self.client.get(reverse("course_list"))
        self.assertContains(listing, "Информатика")
        self.assertNotContains(listing, "Черновик")
        self.assertNotContains(listing, "Курс 10")
        self.assertContains(listing, "Мои курсы")
        self.assertEqual(self.client.get(reverse("course_detail", args=[self.draft.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("course_detail", args=[self.ten_course.pk])).status_code, 404)
        self.client.force_login(self.ten_user)
        self.assertContains(self.client.get(reverse("course_list")), "Курс 10")
        self.assertEqual(self.client.get(reverse("section_detail", args=[self.course.pk, self.section.pk])).status_code, 404)

    def test_opening_a_lesson_updates_only_own_progress_and_completion_is_separate(self):
        self.client.force_login(self.user)
        section_url = reverse("section_detail", args=[self.course.pk, self.section.pk])
        lesson_url = reverse("lesson_detail", args=[self.course.pk, self.section.pk, self.first.pk])
        self.assertContains(self.client.get(section_url), "0 из 2")
        self.assertEqual(LessonProgress.objects.count(), 0)
        opened = self.client.get(lesson_url)
        self.assertContains(opened, "Что такое переменная")
        self.assertContains(opened, "is-opened")
        self.assertContains(opened, "1 из 2")
        self.assertEqual(LessonProgress.objects.count(), 1)
        self.client.get(lesson_url)
        self.assertEqual(LessonProgress.objects.count(), 1)
        self.assertContains(self.client.get(reverse("course_list")), "1</strong> из 2 уроков открыто", html=False)
        self.assertContains(self.client.get(reverse("course_detail", args=[self.course.pk])), "1</strong> из 2 уроков открыто", html=False)
        self.assertNotContains(self.client.get(lesson_url), 'class="lesson-tab is-opened is-completed')

        progress = LessonProgress.objects.get(student=self.student, lesson=self.first)
        progress.completed_at = timezone.now()
        progress.save(update_fields=["completed_at"])
        completed = self.client.get(lesson_url)
        self.assertContains(completed, 'class="lesson-tab is-opened is-completed')
        self.assertContains(completed, "Задания решены")
        self.client.force_login(self.other_user)
        self.assertContains(self.client.get(section_url), "0 из 2")
        self.assertEqual(LessonProgress.objects.count(), 1)

    def test_lesson_must_belong_to_the_selected_section(self):
        other_section = Section.objects.create(course=self.course, position=2, title="Данные")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("lesson_detail", args=[
            self.course.pk, other_section.pk, self.first.pk])).status_code, 404)
        self.assertEqual(LessonProgress.objects.count(), 0)

    def test_teacher_can_manage_courses_and_sections(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get("/admin/courses/course/").status_code, 200)
        self.assertEqual(self.client.get(f"/admin/courses/section/{self.section.pk}/change/").status_code, 200)
        self.assertEqual(self.client.get("/courses/").status_code, 403)

        response = self.client.post("/admin/courses/course/add/", {
            "title": "Новый курс", "subject": "Математика", "grade": "9", "description": "Тестовый курс",
            "sections-TOTAL_FORMS": "1", "sections-INITIAL_FORMS": "0",
            "sections-MIN_NUM_FORMS": "0", "sections-MAX_NUM_FORMS": "1000",
            "sections-0-position": "1", "sections-0-title": "Раздел 1",
        })
        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(title="Новый курс")
        self.assertEqual(course.created_by, self.teacher)
        self.assertEqual(course.sections.get().title, "Раздел 1")
