import json
import re
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.models import Course, Lesson, LessonQuestion, Section
from courses.templatetags.course_content import DIAGRAMS


CONTENT_DIR = Path(__file__).resolve().parents[2] / "content" / "hardware"
DIAGRAM_DIR = Path(__file__).resolve().parents[2] / "static" / "courses" / "diagrams" / "hardware"
SECTIONS = (
    "3.1 Computer architecture",
    "3.2 Input and output devices",
    "3.3 Data storage",
    "3.4 Network hardware",
)
QUESTION_FIELDS = (
    "position", "active", "kind", "points", "prompt", "option_a", "option_b",
    "option_c", "option_d", "correct_option", "accepted_answers", "model_answer",
    "grading_criterion", "student_guidance",
)


def load_lessons():
    files = sorted(CONTENT_DIR.glob("*/lesson.json"))
    if len(files) != 9:
        raise CommandError(f"Expected 9 hardware lesson files in {CONTENT_DIR}; found {len(files)}.")
    lessons = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            content = path.with_name("content.txt").read_text(encoding="utf-8").strip()
            course, section, lesson = data["course"], data["section"], data["lesson"]
            if course["grade"] != 9 or course["subject"] != "Computer Science":
                raise ValueError("Expected grade 9 Computer Science metadata.")
            if section["title"] not in SECTIONS or section["position"] != SECTIONS.index(section["title"]) + 1:
                raise ValueError("Unexpected section title or position.")
            if not lesson["title"] or not content:
                raise ValueError("Lesson title and content are required.")
            images = data["images"]
            slugs = [image["slug"] for image in images]
            markers = re.findall(r"(?m)^\[diagram:([^\]]+)\]$", content)
            if len(slugs) != len(set(slugs)) or sorted(markers) != sorted(slugs):
                raise ValueError("Diagram markers must match the images exactly once.")
            for image in images:
                slug = image["slug"]
                static_path = f"courses/diagrams/hardware/{slug}.svg"
                if (not re.fullmatch(r"[a-z-]+", slug) or image["file"] != f"images/{slug}.svg"
                        or not image["alt"] or not (DIAGRAM_DIR / f"{slug}.svg").is_file()
                        or DIAGRAMS.get(slug, (None, None))[1] != static_path):
                    raise ValueError(f"Missing or unregistered image: {slug}.")
            questions = data["questions"]
            if not questions or [item["position"] for item in questions] != list(range(1, len(questions) + 1)):
                raise ValueError("Question positions must start at 1 and be contiguous.")
            for item in questions:
                if set(item) != set(QUESTION_FIELDS):
                    raise ValueError("Question fields do not match the lesson model.")
                LessonQuestion(**item).full_clean(exclude={"lesson"})
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise CommandError(f"Invalid lesson package {path}: {exc}") from exc
        lessons.append((data, content))
    lessons.sort(key=lambda pair: pair[0]["lesson"]["position"])
    if [data["lesson"]["position"] for data, _ in lessons] != list(range(1, 10)):
        raise CommandError("Hardware lesson positions must be 1 through 9.")
    names = [(data["section"]["title"], data["lesson"]["title"]) for data, _ in lessons]
    if len(names) != len(set(names)) or {name for name, _ in names} != set(SECTIONS):
        raise CommandError("Hardware lessons need unique titles in all four sections.")
    return lessons


def matches(lesson, content, questions):
    if lesson.content != content:
        return False
    existing = list(lesson.questions.order_by("position"))
    return len(existing) == len(questions) and all(
        all(getattr(saved, field) == expected[field] for field in QUESTION_FIELDS)
        for saved, expected in zip(existing, questions)
    )


class Command(BaseCommand):
    help = "Preview or import Chapter 3 Hardware into an existing grade-9 Computer Science course."

    def add_arguments(self, parser):
        parser.add_argument("--course-id", type=int, required=True, help="Existing course ID.")
        parser.add_argument("--apply", action="store_true", help="Save changes; otherwise preview only.")

    def handle(self, *args, **options):
        lessons = load_lessons()
        apply = options["apply"]
        with transaction.atomic():
            course = Course.objects.select_for_update().filter(pk=options["course_id"]).first() if apply else Course.objects.filter(pk=options["course_id"]).first()
            if course is None or course.grade != 9 or course.subject.casefold() != "computer science":
                raise CommandError("Select an existing grade-9 Computer Science course.")
            next_section = max(course.sections.values_list("position", flat=True), default=0) + 1
            states = {}
            for title in SECTIONS:
                found = list(course.sections.filter(title=title))
                if len(found) > 1:
                    raise CommandError(f"More than one section named {title!r}; import refused.")
                section = found[0] if found else None
                existing = list(section.lessons.prefetch_related("questions")) if section else []
                if len(existing) != len({item.title for item in existing}):
                    raise CommandError(f"Duplicate lesson titles in {title!r}; import refused.")
                states[title] = {
                    "section": section,
                    "section_position": section.position if section else next_section,
                    "lessons": {item.title: item for item in existing},
                    "next_lesson": max((item.position for item in existing), default=0) + 1,
                }
                if section is None:
                    next_section += 1
            added = already = 0
            for data, content in lessons:
                title = data["section"]["title"]
                state = states[title]
                lesson_title = data["lesson"]["title"]
                questions = data["questions"]
                existing = state["lessons"].get(lesson_title)
                if existing:
                    if not matches(existing, content, questions):
                        raise CommandError(f"Conflicting existing lesson {lesson_title!r}; import refused.")
                    already += 1
                    continue
                position = state["next_lesson"]
                state["next_lesson"] += 1
                added += 1
                if not apply:
                    self.stdout.write(f"Would add {title} / {position}: {lesson_title} ({len(questions)} questions)")
                    continue
                section = state["section"]
                if section is None:
                    section = Section(course=course, position=state["section_position"], title=title)
                    section.full_clean()
                    section.save()
                    state["section"] = section
                lesson = Lesson(section=section, position=position, title=lesson_title, content=content)
                lesson.full_clean()
                lesson.save()
                for question_data in questions:
                    question = LessonQuestion(lesson=lesson, **question_data)
                    question.full_clean()
                    question.save()
                state["lessons"][lesson_title] = lesson
            self.stdout.write(self.style.SUCCESS(
                f"{added} lesson(s) {'imported' if apply else 'would be imported'}; {already} already imported."
            ))
