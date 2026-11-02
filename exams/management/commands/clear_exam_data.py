from pathlib import PurePosixPath

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.models import (
    Attempt, AttemptVisibilityEvent, Exam, ExamQuestionRule, GradeAppeal,
    Invitation, Question, SharedCode,
)


# Delete dependents first. Student and course tables are deliberately excluded.
EXAM_MODELS = (
    GradeAppeal, AttemptVisibilityEvent, Attempt, Question, Invitation,
    SharedCode, ExamQuestionRule, Exam,
)


class Command(BaseCommand):
    help = "Show or delete all exam records without touching courses or students."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Delete the listed exam records.")

    def handle(self, *args, **options):
        counts = {model: model.objects.count() for model in EXAM_MODELS}
        for model, count in counts.items():
            self.stdout.write(f"{model.__name__}: {count}")

        if not options["apply"]:
            self.stdout.write("Dry run; pass --apply to delete these exam records.")
            return

        files = set(Exam.objects.exclude(syllabus_file="").values_list("syllabus_file", flat=True))
        if any(field.name == "image" for field in Question._meta.fields):
            files.update(Question.objects.exclude(image="").values_list("image", flat=True))
        for name in files:
            parts = PurePosixPath(name).parts
            if len(parts) < 2 or parts[0] not in {"syllabi", "exam_question_images"} or ".." in parts:
                raise CommandError(f"Unexpected exam media path; nothing deleted: {name}")
        with transaction.atomic():
            for model in EXAM_MODELS:
                model.objects.all().delete()
            if any(model.objects.exists() for model in EXAM_MODELS):
                raise CommandError("Exam records remain; deletion was rolled back.")

        for name in files:
            default_storage.delete(name)
        self.stdout.write(self.style.SUCCESS("Exam records deleted. Courses and students preserved."))
