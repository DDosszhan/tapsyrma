from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.models import Course


CONTENT_DIR = Path(__file__).resolve().parents[2] / "content" / "data_representation"
EXPECTED_TITLES = (
    "Number Systems",
    "Addition, Shifts and Negative Numbers",
    "Text, Sound and Images",
    "Data Units and File Size",
    "Compression",
)


class Command(BaseCommand):
    help = "Refresh the original teaching text in the first IGCSE data representation course."

    def add_arguments(self, parser):
        parser.add_argument("--course-id", type=int, default=1)
        parser.add_argument("--apply", action="store_true", help="Save changes; otherwise preview only.")

    def handle(self, *args, **options):
        course = Course.objects.filter(pk=options["course_id"], grade=9,
                                       title="Computer Science: Data Representation").first()
        if course is None:
            raise CommandError("The expected grade 9 course was not found.")
        lessons = list(course.sections.order_by("position").first().lessons.order_by("position"))
        if tuple(lesson.title for lesson in lessons) != EXPECTED_TITLES:
            raise CommandError("Lesson titles/order differ from the expected first chapter; nothing changed.")
        updated = 0
        with transaction.atomic():
            for position, lesson in enumerate(lessons, 1):
                content = (CONTENT_DIR / f"{position}.txt").read_text(encoding="utf-8").strip()
                if lesson.content != content:
                    updated += 1
                    if options["apply"]:
                        type(lesson).objects.filter(pk=lesson.pk).update(content=content)
            if not options["apply"]:
                transaction.set_rollback(True)
        self.stdout.write(f"{updated} lesson(s) {'updated' if options['apply'] else 'would be updated'}; "
                          "questions and submissions untouched.")
