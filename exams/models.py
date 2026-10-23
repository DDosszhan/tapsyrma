from decimal import Decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Exam(models.Model):
    class Grade(models.IntegerChoices):
        NINE = 9, "Grade 9"
        TEN = 10, "Grade 10"

    title = models.CharField("Title", max_length=200)
    subject = models.CharField("Subject", max_length=100)
    grade = models.PositiveSmallIntegerField("Grade", choices=Grade.choices)
    syllabus_file = models.FileField("Syllabus file", upload_to="syllabi/%Y/%m/", blank=True)
    syllabus_text = models.TextField("Syllabus text", blank=True, help_text="Check the extracted text before generating questions.")
    question_count = models.PositiveSmallIntegerField("Total questions")
    auto_structure = models.BooleanField("Allocate question types and points automatically", default=True)
    duration_minutes = models.PositiveSmallIntegerField(
        "Duration (minutes)", default=60, validators=[MinValueValidator(1)],
        help_text="The timer starts when the student selects “Start exam”.")
    total_points = models.DecimalField("Total points", max_digits=6, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, editable=False)
    created_at = models.DateTimeField("Created", auto_now_add=True)
    updated_at = models.DateTimeField("Updated", auto_now=True)
    published = models.BooleanField("Published", default=False)

    class Meta:
        verbose_name = "Exam"
        verbose_name_plural = "Exams"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(grade__in=[9, 10]), name="exam_grade_9_or_10"),
            models.CheckConstraint(condition=models.Q(duration_minutes__gte=1), name="exam_duration_positive"),
        ]

    def __str__(self):
        return f"Grade {self.grade} — {self.title}"

    @property
    def assessment_style(self):
        return "IGCSE" if self.grade == 9 else "AS Level"

    def clean(self):
        errors = {}
        if not self.syllabus_text.strip():
            errors["syllabus_text"] = "Add syllabus text or upload a file with extractable text."
        elif len(self.syllabus_text) > 100_000:
            errors["syllabus_text"] = "Keep only the relevant syllabus excerpt (up to 100,000 characters)."
        if self.question_count is not None and self.question_count < 1:
            errors["question_count"] = "Enter at least one question."
        if self.total_points is not None and self.total_points <= Decimal("0"):
            errors["total_points"] = "Total points must be greater than zero."
        if errors:
            raise ValidationError(errors)


class ExamQuestionRule(models.Model):
    class Kind(models.TextChoices):
        MCQ = "mcq", "MCQ — one correct answer"
        OPEN = "open", "Open response"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="question_rules")
    kind = models.CharField("Question type", max_length=20, choices=Kind.choices)
    count = models.PositiveSmallIntegerField("Count")
    points_each = models.DecimalField("Points per question", max_digits=6, decimal_places=2)

    class Meta:
        verbose_name = "Question type"
        verbose_name_plural = "Question types"
        constraints = [models.UniqueConstraint(fields=["exam", "kind"], name="unique_exam_question_kind")]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.count} × {self.points_each}"

    def clean(self):
        errors = {}
        if self.count is not None and self.count < 1:
            errors["count"] = "Enter at least one question for an included type. Delete the row to exclude it."
        if self.points_each is not None and self.points_each <= Decimal("0"):
            errors["points_each"] = "Points per question must be greater than zero."
        if errors:
            raise ValidationError(errors)


class Question(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    invitation = models.ForeignKey("Invitation", on_delete=models.PROTECT, related_name="questions", null=True, blank=True)
    slot = models.PositiveSmallIntegerField("Position in exam")
    kind = models.CharField("Type", max_length=20, choices=ExamQuestionRule.Kind.choices)
    points = models.PositiveSmallIntegerField("Points")
    subtopic = models.CharField("Subtopic", max_length=200)
    stem = models.TextField("Question")
    options = models.JSONField("Answer options", default=list, blank=True)
    correct_option = models.PositiveSmallIntegerField("Correct answer number", null=True, blank=True)
    rubric = models.JSONField("Criteria", default=list)
    model_answer = models.TextField("Model answer")
    subtopic_explanation = models.TextField("Subtopic explanation")
    answer_explanation = models.TextField("Answer explanation")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slot", "id"]
        verbose_name = "Generated question"
        verbose_name_plural = "Generated questions"
        constraints = [models.UniqueConstraint(fields=["invitation", "slot"], name="unique_invitation_question_slot")]

    def __str__(self):
        return f"{self.exam}: position {self.slot} — {self.subtopic}"


class Student(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="student_profile", verbose_name="User account")
    name = models.CharField("Student name", max_length=200)
    grade = models.PositiveSmallIntegerField("Grade", choices=Exam.Grade.choices)
    external_id = models.CharField("Student ID", max_length=80, unique=True, default=uuid.uuid4, editable=False)
    password_change_required = models.BooleanField("Password change required", default=True)
    anonymized_at = models.DateTimeField("Account deleted", null=True, blank=True)

    class Meta:
        verbose_name = "Student"
        verbose_name_plural = "Students"
        ordering = ["name"]

    def __str__(self):
        if self.anonymized_at:
            return f"Deleted student #{self.pk}"
        return f"{self.name} ({self.user.email if self.user_id else self.external_id})"


class SharedCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField("Start before")
    is_active = models.BooleanField("Active", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Shared code"
        verbose_name_plural = "Shared codes"

    def __str__(self):
        return f"Shared code #{self.pk}"


class Invitation(models.Model):
    class VariantStatus(models.TextChoices):
        PENDING = "pending", "Queued"
        GENERATING = "generating", "Generating"
        READY = "ready", "Ready"
        ERROR = "error", "Generation error"

    exam = models.ForeignKey(Exam, on_delete=models.PROTECT)
    student = models.ForeignKey(Student, on_delete=models.PROTECT)
    shared_code = models.ForeignKey(SharedCode, on_delete=models.PROTECT, related_name="invitations", null=True, blank=True)
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField("Start before")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    variant_status = models.CharField("Question set", max_length=12, choices=VariantStatus.choices, default=VariantStatus.PENDING)
    generation_started_at = models.DateTimeField(null=True, blank=True)
    generation_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Exam code"
        verbose_name_plural = "Exam codes"
        constraints = [models.UniqueConstraint(fields=["shared_code", "student"], name="unique_shared_code_student")]

    def __str__(self):
        return f"{self.student} — {self.exam}"


class Attempt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "In progress"
        PENDING = "pending", "Awaiting grading"
        GRADING = "grading", "Being graded"
        DONE = "done", "Graded"
        ERROR = "error", "Manual review required"

    invitation = models.OneToOneField(Invitation, on_delete=models.PROTECT, related_name="attempt")
    question_ids = models.JSONField(default=list)
    answers = models.JSONField(default=dict)
    grades = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    deadline_at = models.DateTimeField("Finish before", null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    grading_started_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "Attempt"
        verbose_name_plural = "Attempts"
        indexes = [models.Index(fields=["status", "deadline_at"], name="attempt_deadline_idx")]


class AttemptVisibilityEvent(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.PROTECT, related_name="visibility_events")
    event_id = models.UUIDField()
    left_at = models.DateTimeField("Page hidden", auto_now_add=True)
    returned_at = models.DateTimeField("Returned to page", null=True, blank=True)

    class Meta:
        verbose_name = "Exam page exit"
        verbose_name_plural = "Exam page exits"
        ordering = ["left_at"]
        constraints = [models.UniqueConstraint(fields=["attempt", "event_id"], name="unique_attempt_visibility_event")]


class GradeAppeal(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting teacher"
        ACCEPTED = "accepted", "Score changed"
        REJECTED = "rejected", "Score unchanged"

    attempt = models.ForeignKey(Attempt, on_delete=models.PROTECT, related_name="appeals", verbose_name="Attempt")
    question = models.ForeignKey(Question, on_delete=models.PROTECT, verbose_name="Question")
    student_reason = models.TextField("Reason for review")
    student_note = models.TextField("Student note", blank=True)
    original_grade = models.JSONField("Original grade")
    status = models.CharField("Decision", max_length=12, choices=Status.choices, default=Status.PENDING)
    corrected_grade = models.JSONField("Teacher grade", null=True, blank=True)
    teacher_note = models.TextField("Teacher explanation", blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    editable=False, verbose_name="Reviewed by")
    created_at = models.DateTimeField("Filed", auto_now_add=True)
    reviewed_at = models.DateTimeField("Reviewed", null=True, blank=True)

    class Meta:
        verbose_name = "Grade appeal"
        verbose_name_plural = "Grade appeals"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["attempt", "question"], name="unique_attempt_question_appeal")]

    def __str__(self):
        return f"Attempt #{self.attempt_id}, question #{self.question_id}"
