"""Parse teacher-authored fixed exam questions without rewriting their wording."""
from pathlib import Path
import re

from django.core.exceptions import ValidationError

from .syllabus import extract_syllabus_text


XLSX_COLUMNS = (
    "number", "kind", "grading_mode", "points", "topic", "question", "requires_image", "option_1", "option_2",
    "option_3", "option_4", "correct_option", "criteria", "model_answer",
    "topic_explanation", "answer_explanation",
)
MAX_QUESTIONS = 1000
MAX_FILE_BYTES = 10 * 1024 * 1024
QUESTION_HEADER = re.compile(r"(?im)^[ \t]*(?:Question|Вопрос)[ \t]+(\d+)\b[^\r\n]*")
MARKS = re.compile(r"(?i)[\[(][ \t]*(\d+)[ \t]*(?:marks?|балл(?:а|ов)?)[ \t]*[\])]")
KEY_MARKER = re.compile(r"(?im)^[ \t]*\[\[(MODEL ANSWER|CRITERIA|TOPIC EXPLANATION|ANSWER EXPLANATION)\]\][ \t]*$")
DOCUMENT_FOOTER = re.compile(r"(?im)^[ \t]*(?:Assessment configuration|Create all six questions)\b.*$")
TEACHER_LINE = re.compile(r"(?im)^[ \t]*(?:Question[ \t]+type[ \t]*:.*|IMPORTANT[ \t]*:[ \t]*Insert[ \t]+(?:the[ \t]+)?supplied[ \t]+image.*)(?:\r?\n)?")


def _text(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value.strip() if isinstance(value, str) else "" if value is None else str(value).strip()


def _positive_int(value, label, reference):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", _text(value)):
        raise ValidationError(f"{reference}: {label} must be a positive whole number.")
    number = int(_text(value))
    if not 1 <= number <= 32767:
        raise ValidationError(f"{reference}: {label} must be between 1 and 32767.")
    return number


def _criteria(value, points, reference):
    if isinstance(value, list):
        rows = value
    else:
        rows = []
        for line in _text(value).splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*(\d+)\s*:\s*(.+?)\s*", line)
            if not match:
                raise ValidationError(f"{reference}: write each criterion as '1: Description'.")
            rows.append({"criterion": match.group(2), "points": int(match.group(1))})
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("criterion")):
            raise ValidationError(f"{reference}: every criterion needs a description.")
        normalized.append({"criterion": _text(row["criterion"]),
                           "points": _positive_int(row.get("points"), "Criterion points", reference)})
    if normalized and sum(row["points"] for row in normalized) != points:
        raise ValidationError(f"{reference}: criterion points must add up to {points}.")
    return normalized


def normalize_question(data, *, reference="Question"):
    """Return model fields from manual or spreadsheet input (1-based answer key)."""
    slot = _positive_int(data.get("slot"), "Position", reference)
    points = _positive_int(data.get("points"), "Points", reference)
    kind = _text(data.get("kind") or "open").lower()
    if kind not in ("open", "mcq"):
        raise ValidationError(f"{reference}: type must be 'open' or 'mcq'.")
    grading_mode = _text(data.get("grading_mode") or "ai").lower()
    if grading_mode not in ("ai", "manual"):
        raise ValidationError(f"{reference}: grading mode must be 'ai' or 'manual'.")
    stem = _text(data.get("stem"))
    if not stem:
        raise ValidationError(f"{reference}: question text is required.")
    subtopic = _text(data.get("subtopic"))
    if len(subtopic) > 200:
        raise ValidationError(f"{reference}: topic is longer than 200 characters.")
    options = [_text(value) for value in data.get("options", [])]
    model_answer = _text(data.get("model_answer"))
    rubric = _criteria(data.get("rubric"), points, reference)
    if kind == "mcq":
        if grading_mode == "manual":
            raise ValidationError(f"{reference}: multiple-choice questions use automatic grading; choose AI grading.")
        if len(options) != 4 or any(not option for option in options) or len(set(options)) != 4:
            raise ValidationError(f"{reference}: multiple choice needs four different, nonempty options.")
        key = _text(data.get("correct_option_number")).upper()
        if key in ("A", "B", "C", "D"):
            key = str("ABCD".index(key) + 1)
        if key not in ("1", "2", "3", "4"):
            raise ValidationError(f"{reference}: correct option must be 1–4 or A–D.")
        correct_option = int(key) - 1
        model_answer = options[correct_option]
        if not rubric:
            rubric = [{"criterion": "Correct answer", "points": points}]
        elif len(rubric) != 1:
            raise ValidationError(f"{reference}: multiple-choice questions need exactly one marking criterion.")
    else:
        if any(options) or _text(data.get("correct_option_number")) not in ("", "0"):
            raise ValidationError(f"{reference}: open questions cannot have choice options or a choice key.")
        options = []
        correct_option = 0
        if not rubric:
            rubric = [{"criterion": "Correct and complete answer", "points": points}]
    image_flag = data.get("requires_image", False)
    if isinstance(image_flag, str):
        image_flag = image_flag.strip().lower()
        if image_flag not in ("", "0", "no", "false", "n", "нет", "1", "yes", "true", "y", "да"):
            raise ValidationError(f"{reference}: requires_image must be yes or no.")
        image_flag = image_flag in ("1", "yes", "true", "y", "да")
    return {
        "slot": slot, "kind": kind, "grading_mode": grading_mode, "points": points, "subtopic": subtopic, "stem": stem,
        "options": options, "correct_option": correct_option, "rubric": rubric,
        "model_answer": model_answer,
        "subtopic_explanation": _text(data.get("subtopic_explanation")),
        "answer_explanation": _text(data.get("answer_explanation")),
        "requires_image": bool(image_flag),
    }


def validate_question_set(rows, *, require_questions=True):
    rows = sorted(rows, key=lambda row: row["slot"])
    if require_questions and not rows:
        raise ValidationError("Add or upload at least one fixed question before publishing.")
    if len(rows) > MAX_QUESTIONS:
        raise ValidationError(f"Use no more than {MAX_QUESTIONS} questions in one exam.")
    slots = [row["slot"] for row in rows]
    if slots != list(range(1, len(rows) + 1)):
        raise ValidationError("Question positions must be consecutive from 1 without duplicates.")
    stems = [" ".join(row["stem"].casefold().split()) for row in rows]
    if len(stems) != len(set(stems)):
        raise ValidationError("The fixed exam contains duplicate question text.")
    total = sum(row["points"] for row in rows)
    if total > 9999:
        raise ValidationError("The total cannot exceed 9999 points.")
    return rows, len(rows), total


def validate_stored_questions(exam):
    rows = []
    for question in exam.questions.filter(invitation__isnull=True).order_by("slot"):
        row = normalize_question({
            "slot": question.slot, "kind": question.kind, "grading_mode": question.grading_mode,
            "points": question.points,
            "subtopic": question.subtopic, "stem": question.stem, "options": question.options,
            "correct_option_number": question.correct_option + 1 if question.kind == "mcq" and question.correct_option is not None else "",
            "rubric": question.rubric, "model_answer": question.model_answer,
            "subtopic_explanation": question.subtopic_explanation,
            "answer_explanation": question.answer_explanation,
            "requires_image": question.requires_image,
        }, reference=f"Question {question.slot}")
        if question.rubric != row["rubric"] or question.model_answer != row["model_answer"]:
            raise ValidationError(f"Question {question.slot} has an invalid marking key. Save it in the editor first.")
        if question.requires_image and not question.image:
            raise ValidationError(f"Question {question.slot} refers to a supplied image. Attach its image before publishing.")
        rows.append(row)
    return validate_question_set(rows)


def _read_xlsx(upload):
    from openpyxl import load_workbook

    try:
        upload.seek(0)
        book = load_workbook(upload, read_only=True, data_only=True)
        sheet = book.active
        iterator = sheet.iter_rows(values_only=True)
        header = next(iterator, None)
        if not header:
            raise ValidationError("The spreadsheet is empty.")
        names = [_text(value).lower().replace(" ", "_") for value in header]
        if len(names) != len(set(name for name in names if name)):
            raise ValidationError("The spreadsheet has duplicate column names.")
        if not {"points", "question"}.issubset(names):
            raise ValidationError("The first row must include 'points' and 'question' columns. Download the template.")
        rows = []
        for line_number, cells in enumerate(iterator, 2):
            if not any(value is not None and _text(value) for value in cells):
                continue
            if len(rows) >= MAX_QUESTIONS:
                raise ValidationError(f"Use no more than {MAX_QUESTIONS} questions in one exam.")
            record = dict(zip(names, cells))
            options = [record.get(f"option_{number}") for number in range(1, 5)]
            row = normalize_question({
                "slot": record.get("number") or len(rows) + 1,
                "kind": record.get("kind") or ("mcq" if any(_text(option) for option in options) else "open"),
                "grading_mode": record.get("grading_mode") or "ai",
                "points": record.get("points"), "subtopic": record.get("topic"),
                "stem": record.get("question"), "options": options,
                "requires_image": record.get("requires_image"),
                "correct_option_number": record.get("correct_option"),
                "rubric": record.get("criteria"), "model_answer": record.get("model_answer"),
                "subtopic_explanation": record.get("topic_explanation"),
                "answer_explanation": record.get("answer_explanation"),
            }, reference=f"Spreadsheet row {line_number}")
            rows.append(row)
        book.close()
        return validate_question_set(rows)[0]
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Could not read the XLSX file. Download the template and check the workbook.") from exc
    finally:
        upload.seek(0)


def _read_text(upload):
    source = extract_syllabus_text(upload)
    matches = list(QUESTION_HEADER.finditer(source))
    if not matches:
        raise ValidationError("No 'Question 1 ... [3 marks]' headings were found. Use the documented question format.")
    rows = []
    for index, match in enumerate(matches):
        heading = match.group(0)
        marks = MARKS.search(heading)
        if not marks:
            raise ValidationError(f"Question {match.group(1)}: add points to the heading, for example '[3 marks]'.")
        block = source[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(source)].strip()
        footer = DOCUMENT_FOOTER.search(block)
        if footer:
            block = block[:footer.start()].rstrip()
        markers = list(KEY_MARKER.finditer(block))
        raw_stem = block[:markers[0].start()].strip() if markers else block
        requires_image = bool(re.search(r"(?im)^[ \t]*IMPORTANT[ \t]*:[ \t]*Insert[ \t]+(?:the[ \t]+)?supplied[ \t]+image", raw_stem))
        manual = bool(re.search(r"(?im)^[ \t]*Question[ \t]+type[ \t]*:[^\r\n]*(?:file[ \t]+upload|manually[ \t]+marked)", raw_stem))
        stem = TEACHER_LINE.sub("", raw_stem).strip()
        metadata = {}
        for marker_index, marker in enumerate(markers):
            end = markers[marker_index + 1].start() if marker_index + 1 < len(markers) else len(block)
            metadata[marker.group(1).upper()] = block[marker.end():end].strip()
        rows.append(normalize_question({
            "slot": match.group(1), "kind": "open", "points": marks.group(1),
            "grading_mode": "manual" if manual else "ai", "requires_image": requires_image,
            "stem": stem, "model_answer": metadata.get("MODEL ANSWER"),
            "rubric": metadata.get("CRITERIA"),
            "subtopic_explanation": metadata.get("TOPIC EXPLANATION"),
            "answer_explanation": metadata.get("ANSWER EXPLANATION"),
        }, reference=f"Question {match.group(1)}"))
    return validate_question_set(rows)[0]


def read_fixed_questions(upload):
    extension = Path(upload.name).suffix.lower()
    if upload.size == 0 or upload.size > MAX_FILE_BYTES:
        raise ValidationError("The file must be nonempty and no larger than 10 MB.")
    if extension in (".xlsx", ".docx", ".pdf"):
        try:
            upload.seek(0)
            if extension == ".xlsx":
                from openpyxl import load_workbook

                book = load_workbook(upload, read_only=False, data_only=True)
                has_images = any(sheet._images for sheet in book.worksheets)
                book.close()
            elif extension == ".docx":
                from docx import Document
                from docx.opc.constants import RELATIONSHIP_TYPE

                document = Document(upload)
                has_images = any(rel.reltype == RELATIONSHIP_TYPE.IMAGE for rel in document.part.rels.values())
            else:
                from pypdf import PdfReader

                reader = PdfReader(upload)
                has_images = any(len(page.images) for page in reader.pages)
        except Exception as exc:
            raise ValidationError("Could not inspect the uploaded file. Check its format.") from exc
        finally:
            upload.seek(0)
        if has_images:
            raise ValidationError("Embedded images cannot be assigned to questions automatically. Remove them from the file, import the text, then attach each image to its question in the editor.")
    if extension == ".xlsx":
        return _read_xlsx(upload)
    if extension in (".txt", ".pdf", ".docx"):
        return _read_text(upload)
    raise ValidationError("Upload XLSX, TXT, PDF or DOCX.")


def validate_question_image(upload):
    if not upload or not hasattr(upload, "read"):
        return
    if upload.size == 0 or upload.size > MAX_FILE_BYTES:
        raise ValidationError("The image must be nonempty and no larger than 10 MB.")
    extension = Path(upload.name).suffix.lower()
    upload.seek(0)
    header = upload.read(16)
    upload.seek(0)
    valid = (
        (extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
        or (extension in (".jpg", ".jpeg") and header.startswith(b"\xff\xd8\xff"))
        or (extension == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP")
    )
    if not valid:
        raise ValidationError("Upload a PNG, JPEG or WebP image.")
