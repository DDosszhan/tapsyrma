from django.conf import settings
from django.core.exceptions import ValidationError
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


class LessonQuestion(models.Model):
    class Kind(models.TextChoices):
        MCQ = "mcq", "Выбор из четырёх"
        SHORT = "short", "Короткий ответ"
        OPEN = "open", "Развёрнутый ответ"

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="questions", verbose_name="Урок")
    position = models.PositiveSmallIntegerField("Порядок", default=1)
    active = models.BooleanField("Показывать ученикам", default=True)
    kind = models.CharField("Тип", max_length=8, choices=Kind.choices, default=Kind.MCQ)
    prompt = models.TextField("Вопрос")
    points = models.PositiveSmallIntegerField("Баллы", default=1)
    option_a = models.CharField("Вариант 1", max_length=500, blank=True)
    option_b = models.CharField("Вариант 2", max_length=500, blank=True)
    option_c = models.CharField("Вариант 3", max_length=500, blank=True)
    option_d = models.CharField("Вариант 4", max_length=500, blank=True)
    correct_option = models.PositiveSmallIntegerField("Верный вариант", choices=[(n, str(n)) for n in range(1, 5)],
                                                       null=True, blank=True)
    accepted_answers = models.TextField("Верные короткие ответы (по одному на строке)", blank=True)
    model_answer = models.TextField("Образец развёрнутого ответа", blank=True)
    grading_criterion = models.TextField("За что дать баллы", blank=True)
    revision = models.PositiveIntegerField("Версия вопроса", default=1, editable=False)

    class Meta:
        verbose_name = "Задание урока"
        verbose_name_plural = "Задания уроков"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["lesson", "position"], name="unique_lesson_question_position")]

    def __str__(self):
        return f"{self.lesson}: задание {self.position}"

    @property
    def options(self):
        return [self.option_a, self.option_b, self.option_c, self.option_d]

    def clean(self):
        errors = {}
        if not self.prompt.strip():
            errors["prompt"] = "Введите текст вопроса."
        if self.points is None or self.points < 1:
            errors["points"] = "Баллы должны быть больше нуля."
        if self.kind == self.Kind.MCQ:
            for field in ("option_a", "option_b", "option_c", "option_d"):
                if not getattr(self, field).strip():
                    errors[field] = "Заполните все четыре варианта."
            if all(option.strip() for option in self.options) and len({option.strip().casefold() for option in self.options}) < 4:
                errors["option_d"] = "Варианты ответа должны различаться."
            if self.correct_option not in (1, 2, 3, 4):
                errors["correct_option"] = "Выберите номер от 1 до 4."
        elif self.kind == self.Kind.SHORT and not self.accepted_answers.strip():
            errors["accepted_answers"] = "Укажите хотя бы один верный ответ."
        elif self.kind == self.Kind.OPEN:
            if not self.model_answer.strip():
                errors["model_answer"] = "Укажите образец ответа."
            if not self.grading_criterion.strip():
                errors["grading_criterion"] = "Укажите критерий проверки."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        tracked = ("lesson_id", "active", "kind", "prompt", "points", "option_a", "option_b",
                   "option_c", "option_d", "correct_option", "accepted_answers", "model_answer",
                   "grading_criterion")
        previous = type(self).objects.filter(pk=self.pk).values(*tracked).first() if self.pk else None
        changed = previous is None or any(previous[field] != getattr(self, field) for field in tracked)
        old_lesson_id = previous["lesson_id"] if previous else None
        if previous and changed:
            self.revision += 1
        super().save(*args, **kwargs)
        if changed and (self.active or previous and previous["active"]):
            affected = {self.lesson_id, old_lesson_id} - {None}
            LessonProgress.objects.filter(lesson_id__in=affected).update(completed_at=None)
            if previous and previous["active"] and (not self.active or old_lesson_id != self.lesson_id):
                from .jobs import sync_lesson_completion
                for lesson_id in affected:
                    for student_id in LessonProgress.objects.filter(lesson_id=lesson_id).values_list("student_id", flat=True):
                        sync_lesson_completion(student_id, lesson_id)


class LessonAnswer(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает проверки"
        GRADING = "grading", "Проверяется"
        CORRECT = "correct", "Верно"
        INCORRECT = "incorrect", "Есть ошибка"
        REVIEW = "review", "Нужна проверка учителя"
        ERROR = "error", "Ошибка проверки"

    class ExplanationStatus(models.TextChoices):
        NONE = "none", "Не запрошено"
        PENDING = "pending", "Ожидает"
        GENERATING = "generating", "Готовится"
        READY = "ready", "Готово"
        ERROR = "error", "Ошибка"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_answers", verbose_name="Ученик")
    question = models.ForeignKey(LessonQuestion, on_delete=models.PROTECT, related_name="answers", verbose_name="Задание")
    question_revision = models.PositiveIntegerField("Версия задания")
    answer = models.TextField("Ответ ученика")
    status = models.CharField("Проверка", max_length=12, choices=Status.choices, default=Status.PENDING)
    score = models.PositiveSmallIntegerField("Баллы", null=True, blank=True)
    review_reason = models.CharField("Причина проверки", max_length=500, blank=True)
    created_at = models.DateTimeField("Отправлен", auto_now_add=True)
    grading_started_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    explanation_status = models.CharField("Объяснение", max_length=12, choices=ExplanationStatus.choices,
                                          default=ExplanationStatus.NONE)
    explanation = models.TextField("Объяснение ИИ", blank=True)
    explanation_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Ответ на задание урока"
        verbose_name_plural = "Ответы на задания уроков"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"], name="lesson_answer_queue_idx"),
                   models.Index(fields=["explanation_status", "created_at"], name="lesson_explain_queue_idx")]

    def __str__(self):
        return f"{self.student} · {self.question}"
