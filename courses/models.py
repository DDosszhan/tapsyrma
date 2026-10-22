from django.conf import settings
from django.db import models

from exams.models import Exam, Student


class Course(models.Model):
    title = models.CharField("Название", max_length=200)
    subject = models.CharField("Предмет", max_length=100)
    grade = models.PositiveSmallIntegerField("Класс", choices=Exam.Grade.choices)
    description = models.TextField("Описание", blank=True)
    published = models.BooleanField("Опубликован", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "Курс"
        verbose_name_plural = "Курсы"
        ordering = ["grade", "subject", "title"]
        constraints = [models.CheckConstraint(condition=models.Q(grade__in=[9, 10]), name="course_grade_9_or_10")]

    def __str__(self):
        return f"{self.title} · {self.grade} класс"


class Section(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sections", verbose_name="Курс")
    position = models.PositiveSmallIntegerField("Порядок", default=1)
    title = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)

    class Meta:
        verbose_name = "Раздел"
        verbose_name_plural = "Разделы"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["course", "position"], name="unique_course_section_position")]

    def __str__(self):
        return f"{self.course.title} · {self.title}"


class Lesson(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="lessons", verbose_name="Раздел")
    position = models.PositiveSmallIntegerField("Порядок", default=1)
    title = models.CharField("Название", max_length=200)
    content = models.TextField("Материал урока", blank=True)

    class Meta:
        verbose_name = "Урок"
        verbose_name_plural = "Уроки"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["section", "position"], name="unique_section_lesson_position")]

    def __str__(self):
        return f"{self.section.title} · {self.title}"


class LessonProgress(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_progress", verbose_name="Ученик")
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="progress", verbose_name="Урок")
    opened_at = models.DateTimeField("Открыт", auto_now_add=True)
    completed_at = models.DateTimeField("Задания решены", null=True, blank=True)

    class Meta:
        verbose_name = "Прогресс урока"
        verbose_name_plural = "Прогресс уроков"
        constraints = [models.UniqueConstraint(fields=["student", "lesson"], name="unique_student_lesson_progress")]

    def __str__(self):
        return f"{self.student} · {self.lesson}"
