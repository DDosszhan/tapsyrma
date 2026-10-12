from decimal import Decimal
import hashlib
import hmac
from math import prod
import secrets

from django import forms
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .ai import blueprint, generate_round
from .models import Attempt, Exam, ExamQuestionRule, Invitation, Question, Student
from .student_import import read_student_emails
from .syllabus import extract_syllabus_text, validate_syllabus_file


class ExamAdminForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        upload = self.files.get("syllabus_file")
        if upload:
            try:
                validate_syllabus_file(upload)
                text = (cleaned.get("syllabus_text") or "").strip()
                original_text = (self.initial.get("syllabus_text") or "").strip()
                if not text or text == original_text:
                    cleaned["syllabus_text"] = extract_syllabus_text(upload)
            except ValidationError as exc:
                self.add_error("syllabus_file", exc)
        if cleaned.get("published") and not self.instance.pk:
            self.add_error("published", "Сначала сохраните экзамен и сгенерируйте вопросы.")
        if cleaned.get("published") and self.instance.pk:
            try:
                slots = blueprint(self.instance)
                if any(not self.instance.questions.filter(slot=s["slot"]).exists() for s in slots):
                    self.add_error("published", "Сначала сгенерируйте хотя бы один вариант каждого вопроса.")
            except ValidationError as exc:
                self.add_error("published", exc)
        if self.instance.pk and self.instance.published and not cleaned.get("published") and Attempt.objects.filter(invitation__exam=self.instance).exists():
            self.add_error("published", "Экзамен с начатыми сдачами нельзя снять с публикации.")
        return cleaned


class QuestionRuleFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        rows = [form.cleaned_data for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
        if not rows:
            raise ValidationError("Добавьте хотя бы один тип вопросов.")
        kinds = [row["kind"] for row in rows]
        if len(kinds) != len(set(kinds)):
            raise ValidationError("Один тип вопросов нельзя добавить дважды.")

        question_count = sum(row["count"] for row in rows)
        total_points = sum((row["points_each"] * row["count"] for row in rows), Decimal("0"))
        if self.instance.question_count is not None and question_count != self.instance.question_count:
            raise ValidationError(f"Сумма вопросов по типам должна быть {self.instance.question_count}, сейчас {question_count}.")
        if self.instance.total_points is not None and total_points != self.instance.total_points:
            raise ValidationError(f"Сумма баллов по типам должна быть {self.instance.total_points}, сейчас {total_points}.")


class QuestionRuleInline(admin.TabularInline):
    model = ExamQuestionRule
    formset = QuestionRuleFormSet
    extra = 2
    min_num = 1
    validate_min = True
    max_num = len(ExamQuestionRule.Kind.choices)
    verbose_name = "Тип вопросов"
    verbose_name_plural = "Типы вопросов — удалите строку, чтобы исключить тип"


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    can_delete = False
    fields = ["slot", "kind", "points", "subtopic", "stem", "options", "correct_option", "rubric", "model_answer", "subtopic_explanation", "answer_explanation"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    form = ExamAdminForm
    inlines = [QuestionRuleInline, QuestionInline]
    list_display = ["title", "subject", "grade", "question_count", "total_points", "published", "generate_link", "created_at"]
    list_filter = ["grade", "subject"]
    search_fields = ["title", "subject", "syllabus_text"]
    readonly_fields = ["created_by", "created_at", "updated_at"]
    fieldsets = [
        ("Экзамен", {"fields": ["title", "subject", "grade"]}),
        ("Силабус", {"fields": ["syllabus_file", "syllabus_text"], "description": "Загрузите TXT, PDF или DOCX либо вставьте текст. После сохранения проверьте извлеченный текст."}),
        ("Структура", {"fields": ["question_count", "total_points", "published"], "description": "Количество и баллы ниже должны дать указанные здесь итоги. Генерация доступна после сохранения."}),
        ("История", {"fields": ["created_by", "created_at", "updated_at"], "classes": ["collapse"]}),
    ]

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.questions.exists():
            base += ["subject", "grade", "syllabus_file", "syllabus_text", "question_count", "total_points"]
        return base

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if not obj:
            return [inline for inline in instances if not isinstance(inline, QuestionInline)]
        if obj and obj.questions.exists():
            return [inline for inline in instances if not isinstance(inline, QuestionRuleInline)]
        return instances

    def generate_link(self, obj):
        if obj.published:
            return "Опубликован"
        return format_html('<a href="{}">Сгенерировать раунд</a>', reverse("admin:exam_generate", args=[obj.pk]))
    generate_link.short_description = "Генерация"

    def get_urls(self):
        return [path("<int:exam_id>/generate/", self.admin_site.admin_view(self.generate_view), name="exam_generate")] + super().get_urls()

    def generate_view(self, request, exam_id):
        if not request.user.is_superuser:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        exam = Exam.objects.get(pk=exam_id)
        if request.method == "POST":
            if exam.published or Attempt.objects.filter(invitation__exam=exam).exists():
                self.message_user(request, "Опубликованный экзамен нельзя менять.", messages.ERROR)
            else:
                try:
                    Question.objects.bulk_create(generate_round(exam))
                    self.message_user(request, "Раунд вопросов сохранен. Проверьте вопросы и критерии перед публикацией.")
                except ValidationError as exc:
                    self.message_user(request, "; ".join(exc.messages), messages.ERROR)
            return HttpResponseRedirect(reverse("admin:exams_exam_change", args=[exam.pk]))
        from django.shortcuts import render
        counts = [exam.questions.filter(slot=slot).count() for slot in range(1, exam.question_count + 1)]
        return render(request, "admin/exams/generate.html", {"exam": exam, "title": "Генерация вопросов", "opts": self.model._meta,
                                                               "capacity": prod(counts), "used": Attempt.objects.filter(invitation__exam=exam).count()})


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["exam", "slot", "kind", "points", "subtopic", "stem"]
    list_filter = ["exam", "kind", "slot"]
    search_fields = ["stem", "subtopic"]
    readonly_fields = ["exam", "slot", "kind", "points", "subtopic", "stem", "options", "correct_option",
                       "rubric", "model_answer", "subtopic_explanation", "answer_explanation", "created_at"]
    actions = None

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and obj is not None and not obj.exam.published and not Attempt.objects.filter(invitation__exam=obj.exam).exists()


class StudentAdminForm(forms.ModelForm):
    email = forms.EmailField(label="Почта для входа", max_length=150,
                             help_text="При первом входе ученик должен сменить начальный пароль.")

    class Meta:
        model = Student
        fields = ["email", "name", "grade"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["email"].help_text = (
                f"Начальный пароль: {settings.STUDENT_INITIAL_PASSWORD}. При первом входе его нужно сменить."
            )
        if self.instance.pk and self.instance.user_id:
            self.fields["email"].initial = self.instance.user.email

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = get_user_model().objects.filter(Q(username__iexact=email) | Q(email__iexact=email))
        if self.instance.user_id:
            existing = existing.exclude(pk=self.instance.user_id)
        if existing.exists():
            raise ValidationError("Эта почта уже занята другой учетной записью.")
        return email


class StudentImportForm(forms.Form):
    grade = forms.ChoiceField(label="Класс", choices=Exam.Grade.choices)
    file = forms.FileField(label="Файл Excel (.xlsx)")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    form = StudentAdminForm
    change_list_template = "admin/exams/student/change_list.html"
    list_display = ["name", "email_address", "grade"]
    search_fields = ["name", "external_id", "user__email"]
    list_filter = ["grade"]

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")

    @admin.display(description="Почта")
    def email_address(self, obj):
        return obj.user.email if obj.user_id else "Укажите почту в карточке"

    def save_model(self, request, obj, form, change):
        email = form.cleaned_data["email"]
        if obj.user_id:
            user = obj.user
            if user.email != email or user.username != email:
                user.email = user.username = email
                user.save(update_fields=["email", "username"])
        else:
            obj.user = get_user_model().objects.create_user(username=email, email=email,
                                                            password=settings.STUDENT_INITIAL_PASSWORD)
        super().save_model(request, obj, form, change)

    def get_urls(self):
        return [path("import/", self.admin_site.admin_view(self.import_view), name="exams_student_import")] + super().get_urls()

    def import_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = StudentImportForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                rows = read_student_emails(form.cleaned_data["file"])
                created = skipped = 0
                with transaction.atomic():
                    for email, name in rows:
                        existing = get_user_model().objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).first()
                        if existing:
                            if Student.objects.filter(user=existing).exists():
                                skipped += 1
                                continue
                            raise ValidationError(f"Почта {email} уже занята другой учетной записью.")
                        user = get_user_model().objects.create_user(username=email, email=email,
                                                                    password=settings.STUDENT_INITIAL_PASSWORD)
                        Student.objects.create(user=user, name=name, grade=int(form.cleaned_data["grade"]))
                        created += 1
            except ValidationError as exc:
                form.add_error("file", exc)
            else:
                self.message_user(request, f"Создано учеников: {created}; уже были в системе: {skipped}.")
                return HttpResponseRedirect(reverse("admin:exams_student_changelist"))
        context = {**self.admin_site.each_context(request), "title": "Импорт учеников из Excel", "form": form,
                   "opts": self.model._meta, "initial_password": settings.STUDENT_INITIAL_PASSWORD}
        return render(request, "admin/exams/student/import.html", context)


class InvitationForm(forms.ModelForm):
    class Meta:
        model = Invitation
        fields = ["exam", "student", "expires_at"]

    def clean(self):
        data = super().clean()
        exam, student = data.get("exam"), data.get("student")
        if exam and not exam.published:
            self.add_error("exam", "Экзамен еще не опубликован.")
        if exam and student and exam.grade != student.grade:
            self.add_error("student", "Класс ученика не совпадает с экзаменом.")
        if student and not student.user_id:
            self.add_error("student", "Сначала укажите почту ученика в его карточке.")
        if data.get("expires_at") and data["expires_at"] <= timezone.now():
            self.add_error("expires_at", "Укажите время в будущем.")
        return data


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    form = InvitationForm
    list_display = ["student", "exam", "expires_at", "created_at", "attempt_status"]
    readonly_fields = ["created_by", "created_at", "attempt_status"]
    search_fields = ["student__name", "student__user__email", "student__external_id", "exam__title"]
    actions = ["reissue_code"]

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj:
            fields += ["exam", "student"]
        return fields

    def attempt_status(self, obj):
        return obj.attempt.get_status_display() if obj.pk and hasattr(obj, "attempt") else "Не начат"
    attempt_status.short_description = "Сдача"

    def save_model(self, request, obj, form, change):
        if not change:
            code, obj.code_hash = self._new_code()
            obj.created_by = request.user
            super().save_model(request, obj, form, change)
            self.message_user(request, f"Код для {obj.student}: {code}. Сохраните сейчас: повторно он не показывается.")
        else:
            super().save_model(request, obj, form, change)

    @staticmethod
    def _new_code():
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        return code, hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()

    @admin.action(description="Перевыпустить коды для выбранных приглашений")
    def reissue_code(self, request, queryset):
        for obj in queryset.select_related("student"):
            code, digest = self._new_code()
            obj.code_hash = digest
            obj.save(update_fields=["code_hash"])
            self.message_user(request, f"Новый код для {obj.student}: {code}. Старый код больше не работает.")


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ["invitation", "status", "score", "started_at", "submitted_at", "graded_at"]
    list_filter = ["status", "invitation__exam"]
    search_fields = ["invitation__student__name", "invitation__student__user__email",
                     "invitation__student__external_id", "invitation__exam__title"]
    actions = ["retry_grading"]
    readonly_fields = ["invitation", "question_ids", "answers", "grades", "status", "score", "started_at", "submitted_at", "graded_at", "grading_started_at", "error_message"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Повторить проверку ошибочных сдач")
    def retry_grading(self, request, queryset):
        count = queryset.filter(status=Attempt.Status.ERROR).update(status=Attempt.Status.PENDING, error_message="")
        self.message_user(request, f"Отправлено на повторную проверку: {count}.")


admin.site.site_header = "Tapsyrma — управление"
