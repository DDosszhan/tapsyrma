import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from exams.models import Exam, Student


class Course(models.Model):
    title = models.CharField("Title", max_length=200)
    subject = models.CharField("Subject", max_length=100)
    grade = models.PositiveSmallIntegerField("Grade", choices=Exam.Grade.choices)
    description = models.TextField("Description", blank=True)
    published = models.BooleanField("Published", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, editable=False)
    created_at = models.DateTimeField("Created", auto_now_add=True)

    class Meta:
        verbose_name = "Course"
        verbose_name_plural = "Courses"
        ordering = ["grade", "subject", "title"]
        constraints = [models.CheckConstraint(condition=models.Q(grade__in=[9, 10]), name="course_grade_9_or_10")]

    def __str__(self):
        return f"{self.title} · Grade {self.grade}"


class Section(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sections", verbose_name="Course")
    position = models.PositiveSmallIntegerField("Order", default=1)
    title = models.CharField("Title", max_length=200)
    description = models.TextField("Description", blank=True)

    class Meta:
        verbose_name = "Section"
        verbose_name_plural = "Sections"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["course", "position"], name="unique_course_section_position")]

    def __str__(self):
        return f"{self.course.title} · {self.title}"


class Lesson(models.Model):
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="lessons", verbose_name="Section")
    position = models.PositiveSmallIntegerField("Order", default=1)
    title = models.CharField("Title", max_length=200)
    content = models.TextField("Lesson content", blank=True)

    class Meta:
        verbose_name = "Lesson"
        verbose_name_plural = "Lessons"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["section", "position"], name="unique_section_lesson_position")]

    def __str__(self):
        return f"{self.section.title} · {self.title}"


class LessonProgress(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_progress", verbose_name="Student")
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="progress", verbose_name="Lesson")
    opened_at = models.DateTimeField("Opened", auto_now_add=True)
    completed_at = models.DateTimeField("Practice completed", null=True, blank=True)

    class Meta:
        verbose_name = "Lesson progress"
        verbose_name_plural = "Lesson progress records"
        constraints = [models.UniqueConstraint(fields=["student", "lesson"], name="unique_student_lesson_progress")]

    def __str__(self):
        return f"{self.student} · {self.lesson}"


class LessonQuestion(models.Model):
    class Kind(models.TextChoices):
        MCQ = "mcq", "Four-option choice"
        SHORT = "short", "Short answer"
        OPEN = "open", "Extended response"

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="questions", verbose_name="Lesson")
    position = models.PositiveSmallIntegerField("Order", default=1)
    active = models.BooleanField("Visible to students", default=True)
    kind = models.CharField("Type", max_length=8, choices=Kind.choices, default=Kind.MCQ)
    prompt = models.TextField("Question")
    points = models.PositiveSmallIntegerField("Points", default=1)
    option_a = models.CharField("Option 1", max_length=500, blank=True)
    option_b = models.CharField("Option 2", max_length=500, blank=True)
    option_c = models.CharField("Option 3", max_length=500, blank=True)
    option_d = models.CharField("Option 4", max_length=500, blank=True)
    correct_option = models.PositiveSmallIntegerField("Correct option", choices=[(n, str(n)) for n in range(1, 5)],
                                                       null=True, blank=True)
    accepted_answers = models.TextField("Accepted short answers (one per line)", blank=True)
    model_answer = models.TextField("Model extended response", blank=True)
    grading_criterion = models.TextField(
        "Marking criterion", blank=True,
        help_text="For itemized marks, write one line per criterion, for example: 1: Correct method",
    )
    revision = models.PositiveIntegerField("Question revision", default=1, editable=False)

    class Meta:
        verbose_name = "Lesson question"
        verbose_name_plural = "Lesson questions"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["lesson", "position"], name="unique_lesson_question_position")]

    def __str__(self):
        return f"{self.lesson}: question {self.position}"

    @property
    def options(self):
        return [self.option_a, self.option_b, self.option_c, self.option_d]

    def clean(self):
        errors = {}
        if not self.prompt.strip():
            errors["prompt"] = "Enter the question text."
        if self.points is None or self.points < 1:
            errors["points"] = "Points must be greater than zero."
        if self.kind == self.Kind.MCQ:
            for field in ("option_a", "option_b", "option_c", "option_d"):
                if not getattr(self, field).strip():
                    errors[field] = "Fill in all four options."
            if all(option.strip() for option in self.options) and len({option.strip().casefold() for option in self.options}) < 4:
                errors["option_d"] = "Answer options must be different."
            if self.correct_option not in (1, 2, 3, 4):
                errors["correct_option"] = "Choose a number from 1 to 4."
        elif self.kind == self.Kind.SHORT and not self.accepted_answers.strip():
            errors["accepted_answers"] = "Enter at least one accepted answer."
        elif self.kind == self.Kind.OPEN:
            if not self.model_answer.strip():
                errors["model_answer"] = "Enter a model answer."
            if not self.grading_criterion.strip():
                errors["grading_criterion"] = "Enter a marking criterion."
            else:
                try:
                    self.marking_rows()
                except ValidationError as exc:
                    errors["grading_criterion"] = exc.messages[0]
        if errors:
            raise ValidationError(errors)

    def marking_rows(self):
        """Each `1: ...` line is a fixed criterion; older prose remains one criterion."""
        if self.kind != self.Kind.OPEN:
            description = "Select the correct option." if self.kind == self.Kind.MCQ else "Give a correct short answer."
            return [{"criterion": description, "points": self.points}]
        lines = [line.strip() for line in self.grading_criterion.splitlines() if line.strip()]
        if not lines or not re.match(r"^\d+\s*:", lines[0]):
            return [{"criterion": self.grading_criterion.strip(), "points": self.points}]
        rows = []
        for line in lines:
            match = re.fullmatch(r"(\d+)\s*:\s*(\S.*)", line)
            if not match or int(match.group(1)) < 1:
                raise ValidationError("Use one criterion per line: '1: What earns the mark'.")
            rows.append({"criterion": match.group(2), "points": int(match.group(1))})
        if sum(row["points"] for row in rows) != self.points:
            raise ValidationError("Criterion marks must add up to the question points.")
        return rows

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
        PENDING = "pending", "Awaiting grading"
        GRADING = "grading", "Being graded"
        CORRECT = "correct", "Correct"
        INCORRECT = "incorrect", "Needs improvement"
        REVIEW = "review", "Teacher review required"
        ERROR = "error", "Grading error"

    class ExplanationStatus(models.TextChoices):
        NONE = "none", "Not requested"
        PENDING = "pending", "Pending"
        GENERATING = "generating", "Preparing"
        READY = "ready", "Ready"
        ERROR = "error", "Error"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="lesson_answers", verbose_name="Student")
    question = models.ForeignKey(LessonQuestion, on_delete=models.PROTECT, related_name="answers", verbose_name="Question")
    question_revision = models.PositiveIntegerField("Question revision")
    answer = models.TextField("Student answer")
    status = models.CharField("Grading", max_length=12, choices=Status.choices, default=Status.PENDING)
    score = models.PositiveSmallIntegerField("Points", null=True, blank=True)
    criteria_scores = models.JSONField("Marks by criterion", default=list, blank=True)
    review_reason = models.CharField("Review reason", max_length=500, blank=True)
    created_at = models.DateTimeField("Submitted", auto_now_add=True)
    grading_started_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    explanation_status = models.CharField("Explanation", max_length=12, choices=ExplanationStatus.choices,
                                          default=ExplanationStatus.NONE)
    explanation = models.TextField("AI explanation", blank=True)
    explanation_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lesson answer"
        verbose_name_plural = "Lesson answers"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "created_at"], name="lesson_answer_queue_idx"),
                   models.Index(fields=["explanation_status", "created_at"], name="lesson_explain_queue_idx")]

    def __str__(self):
        return f"{self.student} · {self.question}"
