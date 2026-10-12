from decimal import Decimal

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Exam, ExamQuestionRule
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


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    form = ExamAdminForm
    inlines = [QuestionRuleInline]
    list_display = ["title", "subject", "grade", "question_count", "total_points", "created_at"]
    list_filter = ["grade", "subject"]
    search_fields = ["title", "subject", "syllabus_text"]
    readonly_fields = ["created_by", "created_at", "updated_at"]
    fieldsets = [
        ("Экзамен", {"fields": ["title", "subject", "grade"]}),
        ("Силабус", {"fields": ["syllabus_file", "syllabus_text"], "description": "Загрузите TXT, PDF или DOCX либо вставьте текст. После сохранения проверьте извлеченный текст."}),
        ("Структура", {"fields": ["question_count", "total_points"], "description": "Количество и баллы ниже должны дать указанные здесь итоги."}),
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


admin.site.site_header = "Tapsyrma — управление"
