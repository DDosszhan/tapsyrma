from django.contrib import admin

from .models import Course, Lesson, LessonAnswer, LessonProgress, LessonQuestion, Section


class SectionInline(admin.TabularInline):
    model = Section
    extra = 1
    show_change_link = True
    fields = ["position", "title", "description"]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(parent__isnull=True)


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1
    show_change_link = True
    fields = ["position", "title", "content"]


class LessonQuestionInline(admin.StackedInline):
    model = LessonQuestion
    extra = 1
    show_change_link = True
    fields = [("position", "active", "kind", "points"), "prompt",
              ("option_a", "option_b"), ("option_c", "option_d"), "correct_option",
              "accepted_answers", "model_answer", "grading_criterion", "student_guidance"]
    verbose_name = "Question: fill in the fields for the selected type"


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ["title", "subject", "grade", "published", "created_at"]
    list_filter = ["grade", "published", "subject"]
    search_fields = ["title", "subject"]
    readonly_fields = ["created_by", "created_at"]
    inlines = [SectionInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ["title", "course", "parent", "position"]
    list_filter = ["course__grade", "course", "parent"]
    search_fields = ["title", "course__title", "parent__title"]
    inlines = [LessonInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "parent":
            kwargs["queryset"] = Section.objects.filter(parent__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ["title", "section", "position"]
    list_filter = ["section__course"]
    search_fields = ["title", "section__title", "section__course__title"]
    inlines = [LessonQuestionInline]


@admin.register(LessonQuestion)
class LessonQuestionAdmin(admin.ModelAdmin):
    list_display = ["prompt", "lesson", "position", "kind", "points", "active", "revision"]
    list_filter = ["kind", "active", "lesson__section__course"]
    search_fields = ["prompt", "lesson__title"]
    readonly_fields = ["revision"]
    fieldsets = [
        (None, {"fields": ["lesson", "position", "active", "kind", "points", "prompt", "revision"]}),
        ("Four-option choice", {"fields": ["option_a", "option_b", "option_c", "option_d", "correct_option"]}),
        ("Short answer", {"fields": ["accepted_answers"]}),
        ("Extended response", {"fields": ["model_answer", "grading_criterion", "student_guidance"]}),
    ]


@admin.register(LessonAnswer)
class LessonAnswerAdmin(admin.ModelAdmin):
    list_display = ["student", "question", "status", "score", "created_at", "explanation_status"]
    list_filter = ["status", "explanation_status", "question__lesson__section__course"]
    search_fields = ["student__name", "student__user__email", "question__prompt"]
    readonly_fields = [field.name for field in LessonAnswer._meta.fields]
    actions = ["mark_correct", "mark_incorrect", "retry_failed"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Accept selected answers")
    def mark_correct(self, request, queryset):
        from .jobs import sync_lesson_completion
        for answer in queryset.filter(status__in=[LessonAnswer.Status.INCORRECT,
                                                 LessonAnswer.Status.REVIEW,
                                                 LessonAnswer.Status.ERROR]).select_related("question"):
            if answer.question_revision != answer.question.revision:
                continue
            answer.status = LessonAnswer.Status.CORRECT
            answer.score = answer.question.points
            answer.review_reason = "Reviewed by teacher."
            answer.save(update_fields=["status", "score", "review_reason"])
            sync_lesson_completion(answer.student_id, answer.question.lesson_id)

    @admin.action(description="Reject selected answers")
    def mark_incorrect(self, request, queryset):
        from .jobs import sync_lesson_completion
        for answer in queryset.filter(status__in=[LessonAnswer.Status.CORRECT,
                                                 LessonAnswer.Status.INCORRECT,
                                                 LessonAnswer.Status.REVIEW,
                                                 LessonAnswer.Status.ERROR]).select_related("question"):
            answer.status = LessonAnswer.Status.INCORRECT
            answer.score = 0
            answer.review_reason = "Reviewed by teacher."
            answer.save(update_fields=["status", "score", "review_reason"])
            sync_lesson_completion(answer.student_id, answer.question.lesson_id)

    @admin.action(description="Retry failed grading")
    def retry_failed(self, request, queryset):
        queryset.filter(status=LessonAnswer.Status.ERROR).update(status=LessonAnswer.Status.PENDING,
                                                                  review_reason="", grading_started_at=None)


@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ["student", "lesson", "opened_at", "completed_at"]
    list_filter = ["lesson__section__course", "completed_at"]
    search_fields = ["student__name", "student__user__email", "lesson__title"]
    readonly_fields = ["student", "lesson", "opened_at", "completed_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
