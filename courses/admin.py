from django.contrib import admin

from .models import Course, Lesson, LessonProgress, Section


class SectionInline(admin.TabularInline):
    model = Section
    extra = 1
    show_change_link = True
    fields = ["position", "title", "description"]


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1
    fields = ["position", "title", "content"]


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
    list_display = ["title", "course", "position"]
    list_filter = ["course__grade", "course"]
    search_fields = ["title", "course__title"]
    inlines = [LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ["title", "section", "position"]
    list_filter = ["section__course"]
    search_fields = ["title", "section__title", "section__course__title"]


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
