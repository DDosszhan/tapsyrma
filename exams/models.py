from decimal import Decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
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
    auto_structure = models.BooleanField("Распределить типы и баллы автоматически", default=True)
    duration_minutes = models.PositiveSmallIntegerField(
        "Длительность (минут)", default=60, validators=[MinValueValidator(1)],
        help_text="Отсчет начинается, когда ученик нажмет «Начать экзамен».")
    total_points = models.DecimalField("Всего баллов", max_digits=6, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Изменен", auto_now=True)
    published = models.BooleanField("Опубликован", default=False)

    class Meta:
        verbose_name = "Экзамен"
        verbose_name_plural = "Экзамены"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(grade__in=[9, 10]), name="exam_grade_9_or_10"),
            models.CheckConstraint(condition=models.Q(duration_minutes__gte=1), name="exam_duration_positive"),
        ]

    def __str__(self):
        return f"{self.grade} класс — {self.title}"

    @property
    def assessment_style(self):
        return "IGCSE" if self.grade == 9 else "AS Level"

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


class Question(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    invitation = models.ForeignKey("Invitation", on_delete=models.PROTECT, related_name="questions", null=True, blank=True)
    slot = models.PositiveSmallIntegerField("Место в экзамене")
    kind = models.CharField("Тип", max_length=20, choices=ExamQuestionRule.Kind.choices)
    points = models.PositiveSmallIntegerField("Баллы")
    subtopic = models.CharField("Подтема", max_length=200)
    stem = models.TextField("Вопрос")
    options = models.JSONField("Варианты ответа", default=list, blank=True)
    correct_option = models.PositiveSmallIntegerField("Номер верного ответа", null=True, blank=True)
    rubric = models.JSONField("Критерии", default=list)
    model_answer = models.TextField("Образец ответа")
    subtopic_explanation = models.TextField("Объяснение подтемы")
    answer_explanation = models.TextField("Объяснение ответа")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slot", "id"]
        verbose_name = "Сгенерированный вопрос"
        verbose_name_plural = "Сгенерированные вопросы"
        constraints = [models.UniqueConstraint(fields=["invitation", "slot"], name="unique_invitation_question_slot")]

    def __str__(self):
        return f"{self.exam}: место {self.slot} — {self.subtopic}"


class Student(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="student_profile", verbose_name="Учетная запись")
    name = models.CharField("Имя ученика", max_length=200)
    grade = models.PositiveSmallIntegerField("Класс", choices=Exam.Grade.choices)
    external_id = models.CharField("ID ученика", max_length=80, unique=True, default=uuid.uuid4, editable=False)
    password_change_required = models.BooleanField("Нужно сменить пароль", default=True)
    anonymized_at = models.DateTimeField("Аккаунт удалён", null=True, blank=True)

    class Meta:
        verbose_name = "Ученик"
        verbose_name_plural = "Ученики"
        ordering = ["name"]

    def __str__(self):
        if self.anonymized_at:
            return f"Удалённый ученик #{self.pk}"
        return f"{self.name} ({self.user.email if self.user_id else self.external_id})"


class SharedCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField("Начать до")
    is_active = models.BooleanField("Активен", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Общий код"
        verbose_name_plural = "Общие коды"

    def __str__(self):
        return f"Общий код #{self.pk}"


class Invitation(models.Model):
    class VariantStatus(models.TextChoices):
        PENDING = "pending", "В очереди"
        GENERATING = "generating", "Генерируется"
        READY = "ready", "Готов"
        ERROR = "error", "Ошибка генерации"

    exam = models.ForeignKey(Exam, on_delete=models.PROTECT)
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    shared_code = models.ForeignKey(SharedCode, on_delete=models.PROTECT, related_name="invitations", null=True, blank=True)
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField("Начать до")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    variant_status = models.CharField("Вариант", max_length=12, choices=VariantStatus.choices, default=VariantStatus.PENDING)
    generation_started_at = models.DateTimeField(null=True, blank=True)
    generation_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Код на сдачу"
        verbose_name_plural = "Коды на сдачу"
        constraints = [models.UniqueConstraint(fields=["shared_code", "student"], name="unique_shared_code_student")]

    def __str__(self):
        return f"{self.student} — {self.exam}"


class Attempt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "В процессе"
        PENDING = "pending", "Ожидает проверки"
        GRADING = "grading", "Проверяется"
        DONE = "done", "Проверено"
        ERROR = "error", "Нужна ручная проверка"

    invitation = models.OneToOneField(Invitation, on_delete=models.PROTECT, related_name="attempt")
    question_ids = models.JSONField(default=list)
    answers = models.JSONField(default=dict)
    grades = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    deadline_at = models.DateTimeField("Завершить до", null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    grading_started_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "Сдача"
        verbose_name_plural = "Сдачи"
        indexes = [models.Index(fields=["status", "deadline_at"], name="attempt_deadline_idx")]


class AttemptVisibilityEvent(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.PROTECT, related_name="visibility_events")
    event_id = models.UUIDField()
    left_at = models.DateTimeField("Страница скрыта", auto_now_add=True)
    returned_at = models.DateTimeField("Вернулся на страницу", null=True, blank=True)

    class Meta:
        verbose_name = "Уход со страницы экзамена"
        verbose_name_plural = "Уходы со страницы экзамена"
        ordering = ["left_at"]
        constraints = [models.UniqueConstraint(fields=["attempt", "event_id"], name="unique_attempt_visibility_event")]


class GradeAppeal(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает учителя"
        ACCEPTED = "accepted", "Баллы изменены"
        REJECTED = "rejected", "Баллы оставлены"

    attempt = models.ForeignKey(Attempt, on_delete=models.PROTECT, related_name="appeals", verbose_name="Сдача")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, verbose_name="Вопрос")
    student_reason = models.TextField("Почему нужна проверка")
    student_note = models.TextField("Пояснение ученика", blank=True)
    original_grade = models.JSONField("Исходная оценка")
    status = models.CharField("Решение", max_length=12, choices=Status.choices, default=Status.PENDING)
    corrected_grade = models.JSONField("Оценка учителя", null=True, blank=True)
    teacher_note = models.TextField("Объяснение учителя", blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    editable=False, verbose_name="Проверил")
    created_at = models.DateTimeField("Подано", auto_now_add=True)
    reviewed_at = models.DateTimeField("Рассмотрено", null=True, blank=True)

    class Meta:
        verbose_name = "Оспаривание балла"
        verbose_name_plural = "Оспаривания баллов"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["attempt", "question"], name="unique_attempt_question_appeal")]

    def __str__(self):
        return f"Сдача #{self.attempt_id}, вопрос #{self.question_id}"
