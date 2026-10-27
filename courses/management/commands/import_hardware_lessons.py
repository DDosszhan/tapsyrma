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
ROOT_TITLE = "3. Hardware"
ROOT_POSITION = 3
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
    help = "Preview or import Chapter 3 Hardware and its topics into an existing grade-9 course."

    def add_arguments(self, parser):
        parser.add_argument("--course-id", type=int, required=True, help="Existing course ID.")
        parser.add_argument("--apply", action="store_true", help="Save changes; otherwise preview only.")

    def handle(self, *args, **options):
        lessons = load_lessons()
        apply = options["apply"]
        with transaction.atomic():
            courses = Course.objects.select_for_update() if apply else Course.objects
            course = courses.filter(pk=options["course_id"]).first()
            if course is None or course.grade != 9 or course.subject.casefold() != "computer science":
                raise CommandError("Select an existing grade-9 Computer Science course.")
            sections = list(course.sections.select_for_update() if apply else course.sections.all())
            roots = [section for section in sections if section.title == ROOT_TITLE]
            if len(roots) > 1 or roots and roots[0].parent_id is not None:
                raise CommandError(f"Conflicting section {ROOT_TITLE!r}; import refused.")
            root = roots[0] if roots else None
            topics = {}
            for title in SECTIONS:
                found = [section for section in sections if section.title == title]
                if len(found) > 1 or found and found[0].parent_id not in (None, root.pk if root else None):
                    raise CommandError(f"Conflicting topic {title!r}; import refused.")
                topics[title] = found[0] if found else None
            for section in sections:
                if (section.parent_id is None and section.position == ROOT_POSITION
                        and section is not root and section not in topics.values()):
                    raise CommandError(f"Section position {ROOT_POSITION} is occupied; import refused.")
                if (root and section.parent_id == root.pk and section.title not in SECTIONS
                        and section.position in range(1, len(SECTIONS) + 1)):
                    raise CommandError(f"Topic position {section.position} is occupied; import refused.")
            states = {}
            for title in SECTIONS:
                section = topics[title]
                existing = list(section.lessons.prefetch_related("questions")) if section else []
                if len(existing) != len({item.title for item in existing}):
                    raise CommandError(f"Duplicate lesson titles in {title!r}; import refused.")
                states[title] = {
                    "section": section,
                    "lessons": {item.title: item for item in existing},
                    "next_lesson": max((item.position for item in existing), default=0) + 1,
                }
            for data, content in lessons:
                existing = states[data["section"]["title"]]["lessons"].get(data["lesson"]["title"])
                if existing and not matches(existing, content, data["questions"]):
                    raise CommandError(f"Conflicting existing lesson {existing.title!r}; import refused.")

            moved = sum(topic is not None and (root is None or topic.parent_id != root.pk
                        or topic.position != position)
                        for position, topic in enumerate(topics.values(), 1))
            if not apply:
                self.stdout.write(f"Would place {len(SECTIONS)} topics under {ROOT_TITLE}; {moved} existing topic(s) would move.")
            else:
                if root is None:
                    temporary_root_position = max((section.position for section in sections
                                                   if section.parent_id is None), default=0) + 1
                    root = Section(course=course, position=temporary_root_position, title=ROOT_TITLE)
                    root.full_clean()
                    root.save()
                temporary_topic_position = max((section.position for section in sections), default=0) + len(SECTIONS) + 1
                for position, topic in enumerate(topics.values(), 1):
                    if topic is not None and (topic.parent_id != root.pk or topic.position != position):
                        topic.parent = root
                        topic.position = temporary_topic_position
                        topic.full_clean()
                        topic.save(update_fields=["parent", "position"])
                        temporary_topic_position += 1
                for position, title in enumerate(SECTIONS, 1):
                    topic = topics[title]
                    if topic is None:
                        topic = Section(course=course, parent=root, position=position, title=title)
                        topic.full_clean()
                        topic.save()
                    elif topic.position != position:
                        topic.position = position
                        topic.full_clean()
                        topic.save(update_fields=["position"])
                    states[title]["section"] = topic
                if root.position != ROOT_POSITION:
                    root.position = ROOT_POSITION
                    root.full_clean()
                    root.save(update_fields=["position"])

            added = already = 0
            for data, content in lessons:
                title = data["section"]["title"]
                state = states[title]
                lesson_title = data["lesson"]["title"]
                questions = data["questions"]
                existing = state["lessons"].get(lesson_title)
                if existing:
                    already += 1
                    continue
                position = state["next_lesson"]
                state["next_lesson"] += 1
                added += 1
                if not apply:
                    self.stdout.write(f"Would add {title} / {position}: {lesson_title} ({len(questions)} questions)")
                    continue
                section = state["section"]
                lesson = Lesson(section=section, position=position, title=lesson_title, content=content)
                lesson.full_clean()
                lesson.save()
                for question_data in questions:
                    question = LessonQuestion(lesson=lesson, **question_data)
                    question.full_clean()
                    question.save()
                state["lessons"][lesson_title] = lesson
            if apply:
                self.stdout.write(f"{len(SECTIONS)} topic(s) under {ROOT_TITLE}; {moved} existing topic(s) moved.")
            self.stdout.write(self.style.SUCCESS(
                f"{added} lesson(s) {'imported' if apply else 'would be imported'}; {already} already imported."
            ))
