from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Exam(models.Model):
    class Grade(models.IntegerChoices):
        NINE = 9, "9 класс"
        TEN = 10, "10 класс"

    title = models.CharField("Название", max_length=200)
    subject = models.CharField("Предмет", max_length=100)
    grade = models.PositiveSmallIntegerField("Класс", choices=Grade.choices)
    syllabus_file = models.FileField("Файл силабуса", upload_to="syllabi/%Y/%m/", blank=True)
    syllabus_text = models.TextField("Текст силабуса", blank=True, help_text="Проверьте извлеченный текст перед генерацией вопросов.")
    question_count = models.PositiveSmallIntegerField("Всего вопросов")
    total_points = models.DecimalField("Всего баллов", max_digits=6, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Изменен", auto_now=True)

    class Meta:
        verbose_name = "Экзамен"
        verbose_name_plural = "Экзамены"
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(grade__in=[9, 10]), name="exam_grade_9_or_10")]

    def __str__(self):
        return f"{self.grade} класс — {self.title}"

    def clean(self):
        errors = {}
        if not self.syllabus_text.strip():
            errors["syllabus_text"] = "Добавьте текст силабуса или загрузите файл с извлекаемым текстом."
        elif len(self.syllabus_text) > 100_000:
            errors["syllabus_text"] = "Оставьте только нужный фрагмент силабуса (до 100 000 символов)."
        if self.question_count is not None and self.question_count < 1:
            errors["question_count"] = "Укажите хотя бы один вопрос."
        if self.total_points is not None and self.total_points <= Decimal("0"):
            errors["total_points"] = "Общий балл должен быть больше нуля."
        if errors:
            raise ValidationError(errors)


class ExamQuestionRule(models.Model):
    class Kind(models.TextChoices):
        MCQ = "mcq", "MCQ — один правильный ответ"
        OPEN = "open", "Открытый ответ"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="question_rules")
    kind = models.CharField("Тип вопроса", max_length=20, choices=Kind.choices)
    count = models.PositiveSmallIntegerField("Количество")
    points_each = models.DecimalField("Баллов за каждый вопрос", max_digits=6, decimal_places=2)

    class Meta:
        verbose_name = "Тип вопросов"
        verbose_name_plural = "Типы вопросов"
        constraints = [models.UniqueConstraint(fields=["exam", "kind"], name="unique_exam_question_kind")]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.count} × {self.points_each}"

    def clean(self):
        errors = {}
        if self.count is not None and self.count < 1:
            errors["count"] = "Для включенного типа укажите хотя бы один вопрос. Удалите строку, чтобы исключить тип."
        if self.points_each is not None and self.points_each <= Decimal("0"):
            errors["points_each"] = "Баллы за вопрос должны быть больше нуля."
        if errors:
            raise ValidationError(errors)
