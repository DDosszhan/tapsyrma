from decimal import Decimal
import hashlib
import hmac
import secrets
import uuid

from django import forms
from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
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
from django.utils.html import format_html_join
from .ai import blueprint
from .models import Attempt, AttemptVisibilityEvent, Exam, ExamQuestionRule, GradeAppeal, Invitation, Question, SharedCode, Student
from .student_import import read_student_emails
from .syllabus import extract_syllabus_text, validate_syllabus_file


def new_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        digest = hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()
        if not Invitation.objects.filter(code_hash=digest).exists() and not SharedCode.objects.filter(code_hash=digest).exists():
            return code, digest


def automatic_rules(count, total_points):
    if count < 1 or total_points != int(total_points) or total_points < count:
        raise ValidationError("Для автоматической структуры нужны целые баллы: не меньше одного за каждый вопрос.")
    total = int(total_points)
    for mcq_count in sorted(range(count), key=lambda value: (abs(value * 4 - count), value)):
        open_count = count - mcq_count
        open_points, remainder = divmod(total - mcq_count, open_count)
        if open_points >= 1 and remainder == 0:
            return ([(ExamQuestionRule.Kind.MCQ, mcq_count, 1)] if mcq_count else []) + [
                (ExamQuestionRule.Kind.OPEN, open_count, open_points)]
    raise ValidationError("Не удалось распределить баллы по вопросам.")


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
        if cleaned.get("auto_structure") and cleaned.get("question_count") is not None and cleaned.get("total_points") is not None:
            try:
                automatic_rules(cleaned["question_count"], cleaned["total_points"])
            except ValidationError as exc:
                self.add_error("total_points", exc)
        if cleaned.get("published") and not self.instance.pk:
            self.add_error("published", "Сначала сохраните экзамен и его структуру вопросов.")
        if cleaned.get("published") and self.instance.pk:
            try:
                blueprint(self.instance)
            except ValidationError as exc:
                self.add_error("published", exc)
        if self.instance.pk and self.instance.published and not cleaned.get("published") and Invitation.objects.filter(exam=self.instance).exists():
            self.add_error("published", "Экзамен с выданными кодами нельзя снять с публикации.")
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
    list_display = ["title", "subject", "grade", "question_count", "duration_minutes", "total_points", "published", "created_at"]
    list_filter = ["grade", "subject"]
    search_fields = ["title", "subject", "syllabus_text"]
    readonly_fields = ["created_by", "created_at", "updated_at"]
    fieldsets = [
        ("Экзамен", {"fields": ["title", "subject", "grade"]}),
        ("Силабус", {"fields": ["syllabus_file", "syllabus_text"], "description": "Загрузите TXT, PDF или DOCX либо вставьте текст. После сохранения проверьте извлеченный текст."}),
        ("Структура", {"fields": ["question_count", "total_points", "duration_minutes", "auto_structure", "published"], "description": "Автоматически: около 25% MCQ, остальные открытые; баллы подбираются по вашим итогам. Для ручной настройки снимите галочку и заполните типы вопросов ниже."}),
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

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if form.instance.auto_structure:
            form.instance.question_rules.all().delete()
            ExamQuestionRule.objects.bulk_create([
                ExamQuestionRule(exam=form.instance, kind=kind, count=count, points_each=points)
                for kind, count, points in automatic_rules(form.instance.question_count, form.instance.total_points)
            ])

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and (obj.published or obj.questions.exists() or obj.invitation_set.exists()):
            base += ["subject", "grade", "syllabus_file", "syllabus_text", "question_count", "total_points", "auto_structure"]
        return base

    def get_inline_instances(self, request, obj=None):
        instances = super().get_inline_instances(request, obj)
        if (request.method == "POST" and request.POST.get("auto_structure") == "on") or (
            obj and (obj.published or obj.questions.exists() or obj.invitation_set.exists())
        ):
            return []
        return instances

    class Media:
        js = ("exams/exam_admin_v2.js",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["exam", "invitation", "slot", "kind", "points", "subtopic", "stem"]
    list_filter = ["exam", "kind", "slot"]
    search_fields = ["stem", "subtopic", "invitation__student__name", "invitation__student__user__email"]
    readonly_fields = ["exam", "invitation", "slot", "kind", "points", "subtopic", "stem", "options", "correct_option",
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
        return request.user.is_superuser and obj is not None and not obj.invitation_id and not obj.exam.published and not Attempt.objects.filter(invitation__exam=obj.exam).exists()


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
    actions = ["reset_passwords", "anonymize_accounts"]

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(anonymized_at__isnull=True).select_related("user")

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("reset_passwords", None)
            actions.pop("anonymize_accounts", None)
        return actions

    @admin.action(description="Сбросить пароль до начального и потребовать смену")
    def reset_passwords(self, request, queryset):
        if not request.user.is_superuser:
            raise PermissionDenied
        count = 0
        with transaction.atomic():
            for student in queryset.filter(user__isnull=False).select_related("user"):
                student.user.set_password(settings.STUDENT_INITIAL_PASSWORD)
                student.user.save(update_fields=["password"])
                student.password_change_required = True
                student.save(update_fields=["password_change_required"])
                count += 1
        self.message_user(request, f"Пароль сброшен у {count} учеников. При входе они должны сменить его.")

    @admin.action(description="Удалить вход и обезличить историю")
    def anonymize_accounts(self, request, queryset):
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.POST.get("confirm_anonymize") != "yes":
            return render(request, "admin/exams/student/confirm_anonymize.html", {
                **self.admin_site.each_context(request), "title": "Удаление аккаунтов учеников",
                "opts": self.model._meta, "count": queryset.count(),
                "selected_ids": request.POST.getlist(ACTION_CHECKBOX_NAME),
                "select_across": request.POST.get("select_across", "0"),
                "action_checkbox_name": ACTION_CHECKBOX_NAME,
            })
        count = 0
        with transaction.atomic():
            for student in queryset.select_related("user"):
                user = student.user if student.user_id else None
                if user and (user.is_staff or user.is_superuser):
                    raise PermissionDenied("Нельзя удалить аккаунт сотрудника через список учеников.")
                student.user = None
                student.name = "Удалённый ученик"
                student.external_id = str(uuid.uuid4())
                student.anonymized_at = timezone.now()
                student.password_change_required = False
                student.save(update_fields=["user", "name", "external_id", "anonymized_at", "password_change_required"])
                if user:
                    user.delete()
                count += 1
        self.message_user(request, f"Удалено аккаунтов: {count}. История сдач сохранена без имени и почты.")

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
    list_display = ["student", "exam", "shared_code", "expires_at", "variant_status", "created_at", "attempt_status"]
    readonly_fields = ["created_by", "created_at", "shared_code", "variant_status", "generation_error", "attempt_status"]
    search_fields = ["student__name", "student__user__email", "student__external_id", "exam__title"]
    actions = ["reissue_code", "retry_generation"]

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
            code, obj.code_hash = new_code()
            obj.created_by = request.user
            super().save_model(request, obj, form, change)
            self.message_user(request, f"Код для {obj.student}: {code}. Сохраните сейчас: повторно он не показывается.")
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="Перевыпустить коды для выбранных приглашений")
    def reissue_code(self, request, queryset):
        for obj in queryset.filter(shared_code__isnull=True).select_related("student"):
            code, digest = new_code()
            obj.code_hash = digest
            obj.save(update_fields=["code_hash"])
            self.message_user(request, f"Новый код для {obj.student}: {code}. Старый код больше не работает.")

    @admin.action(description="Повторить генерацию ошибочных вариантов")
    def retry_generation(self, request, queryset):
        count = queryset.filter(variant_status=Invitation.VariantStatus.ERROR, attempt__isnull=True).update(
            variant_status=Invitation.VariantStatus.PENDING, generation_error="", generation_started_at=None)
        self.message_user(request, f"Отправлено на повторную генерацию: {count}.")


class SharedCodeForm(forms.ModelForm):
    class Meta:
        model = SharedCode
        fields = ["expires_at", "is_active"]

    def clean_expires_at(self):
        expires_at = self.cleaned_data["expires_at"]
        if expires_at <= timezone.now() and (not self.instance.pk or "expires_at" in self.changed_data):
            raise ValidationError("Укажите время в будущем.")
        return expires_at


@admin.register(SharedCode)
class SharedCodeAdmin(admin.ModelAdmin):
    form = SharedCodeForm
    exclude = ["code_hash"]
    list_display = ["id", "expires_at", "is_active", "redeemed_count", "created_at"]
    readonly_fields = ["created_by", "created_at", "redeemed_count"]
    actions = ["reissue_code"]

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj and obj.invitations.exists():
            fields.append("expires_at")
        return fields

    @admin.display(description="Выбрали экзамен")
    def redeemed_count(self, obj):
        return obj.invitations.count() if obj.pk else 0

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            code, obj.code_hash = new_code()
            obj.created_by = request.user
            super().save_model(request, obj, form, change)
            self.message_user(request, f"Общий код: {code}. Сохраните сейчас: повторно он не показывается.")
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="Перевыпустить выбранные общие коды")
    def reissue_code(self, request, queryset):
        for obj in queryset:
            code, obj.code_hash = new_code()
            obj.save(update_fields=["code_hash"])
            self.message_user(request, f"Новый общий код #{obj.pk}: {code}. Старый больше не работает.")


class AttemptVisibilityEventInline(admin.TabularInline):
    model = AttemptVisibilityEvent
    extra = 0
    can_delete = False
    fields = ["left_at", "returned_at", "duration"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Длительность")
    def duration(self, obj):
        return f"{int((obj.returned_at - obj.left_at).total_seconds())} с" if obj.returned_at else "Нет сигнала возврата"


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ["invitation", "status", "score", "visibility_count", "started_at", "submitted_at", "graded_at"]
    list_filter = ["status", "invitation__exam"]
    inlines = [AttemptVisibilityEventInline]
    search_fields = ["invitation__student__name", "invitation__student__user__email",
                     "invitation__student__external_id", "invitation__exam__title"]
    actions = ["retry_grading"]
    readonly_fields = ["invitation", "question_ids", "answers", "grades", "status", "score", "started_at", "submitted_at", "graded_at", "grading_started_at", "error_message"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Уходы со страницы")
    def visibility_count(self, obj):
        return obj.visibility_events.count()

    @admin.action(description="Повторить проверку ошибочных сдач")
    def retry_grading(self, request, queryset):
        count = queryset.filter(status=Attempt.Status.ERROR).update(status=Attempt.Status.PENDING, error_message="")
        self.message_user(request, f"Отправлено на повторную проверку: {count}.")


class GradeAppealReviewForm(forms.ModelForm):
    decision = forms.ChoiceField(label="Решение учителя", choices=[
        (GradeAppeal.Status.ACCEPTED, "Изменить баллы"),
        (GradeAppeal.Status.REJECTED, "Оставить баллы"),
    ])
    criterion_scores = forms.CharField(
        label="Баллы по критериям", required=False,
        help_text="При изменении укажите баллы в порядке критериев через запятую, например: 1, 0, 2.")

    class Meta:
        model = GradeAppeal
        fields = ["teacher_note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["criterion_scores"].initial = ", ".join(
                str(value) for value in self.instance.original_grade.get("criteria", []))

    def clean(self):
        data = super().clean()
        if not (data.get("teacher_note") or "").strip():
            self.add_error("teacher_note", "Объясните ученику принятое решение.")
        if data.get("decision") == GradeAppeal.Status.ACCEPTED:
            try:
                scores = [int(value.strip()) for value in data.get("criterion_scores", "").split(",")]
            except ValueError:
                scores = []
            rubric = self.instance.question.rubric
            if len(scores) != len(rubric) or any(
                score < 0 or score > criterion["points"] for score, criterion in zip(scores, rubric)
            ):
                self.add_error("criterion_scores", "Укажите по одному целому баллу в пределах каждого критерия.")
            else:
                data["parsed_scores"] = scores
        return data


@admin.register(GradeAppeal)
class GradeAppealAdmin(admin.ModelAdmin):
    form = GradeAppealReviewForm
    list_display = ["id", "attempt", "question", "status", "created_at", "reviewed_at"]
    list_filter = ["status", "attempt__invitation__exam"]
    search_fields = ["attempt__invitation__student__name", "attempt__invitation__student__user__email",
                     "attempt__invitation__exam__title", "question__stem"]
    readonly_fields = ["attempt", "question_text", "student_answer", "student_reason", "student_note", "original_grade",
                       "rubric_details", "status", "reviewed_by", "created_at", "reviewed_at"]

    def get_fields(self, request, obj=None):
        fields = ["attempt", "question_text", "student_answer", "student_reason", "student_note", "original_grade",
                  "rubric_details", "status"]
        if obj and obj.status == GradeAppeal.Status.PENDING:
            fields += ["decision", "criterion_scores", "teacher_note"]
        elif obj:
            fields += ["corrected_grade", "teacher_note"]
        return fields + ["reviewed_by", "created_at", "reviewed_at"]

    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj and obj.status != GradeAppeal.Status.PENDING:
            fields += ["corrected_grade", "teacher_note"]
        return fields

    @admin.display(description="Текст вопроса")
    def question_text(self, obj):
        return obj.question.stem

    @admin.display(description="Ответ ученика")
    def student_answer(self, obj):
        value = obj.attempt.answers.get(str(obj.question_id), "")
        if obj.question.kind == "mcq" and value.isdecimal() and int(value) < len(obj.question.options):
            return obj.question.options[int(value)]
        return value or "Ответ не дан"

    @admin.display(description="Критерии")
    def rubric_details(self, obj):
        return format_html_join("", "<div>{} — до {} баллов</div>",
                                ((row["criterion"], row["points"]) for row in obj.question.rubric))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (obj is None or obj.status == GradeAppeal.Status.PENDING)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            attempt = Attempt.objects.select_for_update().get(pk=obj.attempt_id)
            appeal = GradeAppeal.objects.select_for_update().get(pk=obj.pk)
            if appeal.status != GradeAppeal.Status.PENDING or attempt.status != Attempt.Status.DONE:
                raise PermissionDenied("Это оспаривание уже рассмотрено или сдача не проверена.")
            obj.student_note = appeal.student_note
            obj.status = form.cleaned_data["decision"]
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
            if obj.status == GradeAppeal.Status.ACCEPTED:
                scores = form.cleaned_data["parsed_scores"]
                previous = attempt.grades[str(obj.question_id)]["score"]
                corrected = {"score": sum(scores), "criteria": scores,
                             "explanation": form.cleaned_data["teacher_note"].strip()}
                grades = dict(attempt.grades)
                grades[str(obj.question_id)] = corrected
                attempt.grades = grades
                attempt.score += corrected["score"] - previous
                attempt.save(update_fields=["grades", "score"])
                obj.corrected_grade = corrected
            super().save_model(request, obj, form, change)


admin.site.site_header = "Tapsyrma — управление"
