import json
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.models import Course, Lesson, LessonQuestion, Section
from courses.templatetags.course_content import DIAGRAMS

from .import_hardware_lessons import QUESTION_FIELDS, matches


CONTENT_DIR = Path(__file__).resolve().parents[2] / "content" / "as_hardware"
DIAGRAM_DIR = Path(__file__).resolve().parents[2] / "static" / "courses" / "diagrams" / "as_hardware"
COURSE_META = {
    "title": "Cambridge International AS Level Computer Science 9618 (2027–2029)",
    "subject": "Computer Science",
    "grade": 10,
}
ROOT_TITLE = "3. Hardware"
TOPICS = (
    "3.1 Computers and their components",
    "3.2 Logic Gates and Logic Circuits",
)


def load_lessons():
    files = sorted(CONTENT_DIR.glob("*/lesson.json"))
    if len(files) != 5:
        raise CommandError(f"Expected 5 AS Hardware lessons in {CONTENT_DIR}; found {len(files)}.")
    lessons = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            content = path.with_name("content.txt").read_text(encoding="utf-8").strip()
            section, lesson = data["section"], data["lesson"]
            if data["course"] != COURSE_META:
                raise ValueError("Unexpected course metadata.")
            if (section["title"] not in TOPICS
                    or section["position"] != TOPICS.index(section["title"]) + 1):
                raise ValueError("Unexpected topic title or position.")
            if not lesson["title"] or not content:
                raise ValueError("Lesson title and content are required.")
            images = data["images"]
            slugs = [image["slug"] for image in images]
            markers = re.findall(r"(?m)^\[diagram:([^\]]+)\]$", content)
            if len(slugs) != 2 or len(set(slugs)) != 2 or sorted(markers) != sorted(slugs):
                raise ValueError("Two distinct diagram markers must match the images exactly once.")
            for image in images:
                slug = image["slug"]
                if (not re.fullmatch(r"as-[a-z-]+", slug)
                        or image["file"] != f"images/{slug}.svg"
                        or not image["alt"]
                        or not (DIAGRAM_DIR / f"{slug}.svg").is_file()
                        or DIAGRAMS.get(slug) != (
                            image["alt"], f"courses/diagrams/as_hardware/{slug}.svg"
                        )):
                    raise ValueError(f"Missing or unregistered AS diagram: {slug}.")
            questions = data["questions"]
            if not questions or [item["position"] for item in questions] != list(range(1, len(questions) + 1)):
                raise ValueError("Question positions must start at 1 and be contiguous.")
            for item in questions:
                if set(item) != set(QUESTION_FIELDS):
                    raise ValueError("Question fields do not match the lesson model.")
                LessonQuestion(**item).full_clean(exclude={"lesson"})
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise CommandError(f"Invalid AS lesson package {path}: {exc}") from exc
        lessons.append((data, content))
    lessons.sort(key=lambda pair: pair[0]["lesson"]["position"])
    if [data["lesson"]["position"] for data, _ in lessons] != list(range(1, 6)):
        raise CommandError("AS lesson positions must be 1 through 5.")
    if ([data["section"]["title"] for data, _ in lessons]
            != [TOPICS[0]] * 3 + [TOPICS[1]] * 2):
        raise CommandError("Expected three lessons in topic 3.1 and two in topic 3.2.")
    if len({data["lesson"]["title"] for data, _ in lessons}) != 5:
        raise CommandError("AS lesson titles must be unique.")
    return lessons


class Command(BaseCommand):
    help = "Preview or import AS 9618 Chapter 3 into a grade-10 draft course; publish on a later run."

    def add_arguments(self, parser):
        parser.add_argument("--creator-id", type=int, help="Active superuser ID, required to create the course.")
        parser.add_argument("--apply", action="store_true", help="Save changes; otherwise preview only.")
        parser.add_argument("--publish", action="store_true", help="Publish an already imported course with --apply.")

    def handle(self, *args, **options):
        if options["publish"] and not options["apply"]:
            raise CommandError("--publish requires --apply.")
        lessons = load_lessons()
        apply = options["apply"]
        with transaction.atomic():
            queryset = Course.objects.select_for_update() if apply else Course.objects
            candidates = list(queryset.filter(title=COURSE_META["title"]))
            if len(candidates) > 1:
                raise CommandError("More than one AS course has this title; import refused.")
            course = candidates[0] if candidates else None
            if course and (course.grade != 10 or course.subject != COURSE_META["subject"]):
                raise CommandError("Existing AS course metadata differs from the package.")
            creator = None
            if course is None:
                if options["creator_id"] is None:
                    raise CommandError("Pass --creator-id of an active superuser to create the AS course.")
                creator = get_user_model().objects.filter(
                    pk=options["creator_id"], is_active=True, is_superuser=True,
                ).first()
                if creator is None:
                    raise CommandError("--creator-id must identify an active superuser.")
            sections = list(course.sections.select_for_update() if apply else course.sections.all()) if course else []
            roots = [section for section in sections if section.title == ROOT_TITLE]
            if len(roots) > 1 or roots and (roots[0].parent_id is not None or roots[0].position != 3):
                raise CommandError(f"Conflicting chapter {ROOT_TITLE!r}; import refused.")
            root = roots[0] if roots else None
            if any(section.parent_id is None and section.position == 3 and section is not root for section in sections):
                raise CommandError("Chapter position 3 is occupied; import refused.")
            topics = {}
            for position, title in enumerate(TOPICS, 1):
                found = [section for section in sections if section.title == title]
                if len(found) > 1 or found and (root is None or found[0].parent_id != root.pk
                                               or found[0].position != position):
                    raise CommandError(f"Conflicting topic {title!r}; import refused.")
                if root and any(section.parent_id == root.pk and section.position == position
                                and section not in found for section in sections):
                    raise CommandError(f"Topic position {position} is occupied; import refused.")
                topics[title] = found[0] if found else None
            expected = []
            counters = {title: 0 for title in TOPICS}
            for data, content in lessons:
                title = data["section"]["title"]
                counters[title] += 1
                expected.append((data, content, counters[title]))
            expected_titles = {data["lesson"]["title"] for data, _, _ in expected}
            matching_lessons = [
                lesson for section in sections for lesson in section.lessons.prefetch_related("questions")
                if lesson.title in expected_titles
            ]
            existing_lessons = {lesson.title: lesson for lesson in matching_lessons}
            if len(existing_lessons) != len(matching_lessons):
                raise CommandError("Duplicate AS lesson titles in this course; import refused.")
            missing = []
            for data, content, position in expected:
                title = data["section"]["title"]
                lesson = existing_lessons.get(data["lesson"]["title"])
                if lesson:
                    if lesson.section_id != (topics[title].pk if topics[title] else None) or lesson.position != position:
                        raise CommandError(f"Conflicting lesson location {lesson.title!r}; import refused.")
                    if not matches(lesson, content, data["questions"]):
                        raise CommandError(f"Conflicting lesson content {lesson.title!r}; import refused.")
                else:
                    if topics[title] and topics[title].lessons.filter(position=position).exists():
                        raise CommandError(f"Lesson position {position} is occupied in {title!r}.")
                    missing.append((data, content, position))
            if options["publish"] and (course is None or missing):
                raise CommandError("Import all five lessons as a draft, then publish in a separate --apply run.")
            if not apply:
                if course is None:
                    self.stdout.write(f"Would create draft course: {COURSE_META['title']}")
                if root is None:
                    self.stdout.write(f"Would add chapter {ROOT_TITLE} with {len(TOPICS)} topics.")
                for data, _, position in missing:
                    self.stdout.write(f"Would add {data['section']['title']} / {position}: {data['lesson']['title']}")
                self.stdout.write(f"{len(missing)} lesson(s) would be imported; {5 - len(missing)} already imported.")
                return
            if course is None:
                course = Course(created_by=creator, published=False, **COURSE_META)
                course.full_clean()
                course.save()
            if root is None:
                root = Section(course=course, position=3, title=ROOT_TITLE)
                root.full_clean()
                root.save()
            for position, title in enumerate(TOPICS, 1):
                if topics[title] is None:
                    topic = Section(course=course, parent=root, position=position, title=title)
                    topic.full_clean()
                    topic.save()
                    topics[title] = topic
            for data, content, position in missing:
                lesson = Lesson(section=topics[data["section"]["title"]], position=position,
                                title=data["lesson"]["title"], content=content)
                lesson.full_clean()
                lesson.save()
                for item in data["questions"]:
                    question = LessonQuestion(lesson=lesson, **item)
                    question.full_clean()
                    question.save()
            if options["publish"] and not course.published:
                course.published = True
                course.save(update_fields=["published"])
            self.stdout.write(self.style.SUCCESS(
                f"Course {course.pk}: {len(missing)} lesson(s) imported; {5 - len(missing)} already imported; "
                f"{'published' if course.published else 'draft'}."
            ))
