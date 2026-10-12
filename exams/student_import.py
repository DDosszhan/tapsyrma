from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


def read_student_emails(upload):
    if not upload.name.lower().endswith(".xlsx") or upload.size > 2_000_000:
        raise ValidationError("Загрузите файл .xlsx размером до 2 МБ.")
    try:
        book = load_workbook(upload, read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError, EOFError, ParseError) as exc:
        raise ValidationError("Не удалось открыть файл Excel.") from exc
    try:
        try:
            rows, seen = [], set()
            for line, cells in enumerate(book.active.iter_rows(values_only=True), start=1):
                if line > 1001:
                    raise ValidationError("В одном файле допускается до 1000 учеников.")
                raw = cells[0] if cells else None
                if line == 1 and str(raw).strip().lower() in {"email", "e-mail", "почта"}:
                    continue
                if raw is None or not str(raw).strip():
                    if any(value is not None and str(value).strip() for value in cells[1:]):
                        raise ValidationError(f"Строка {line}: укажите почту в первом столбце.")
                    continue
                email = str(raw).strip().lower()
                try:
                    validate_email(email)
                except ValidationError as exc:
                    raise ValidationError(f"Строка {line}: некорректная почта.") from exc
                if len(email) > 150:
                    raise ValidationError(f"Строка {line}: почта слишком длинная (до 150 символов).")
                if email in seen:
                    raise ValidationError(f"Строка {line}: почта повторяется в файле.")
                seen.add(email)
                name = str(cells[1]).strip() if len(cells) > 1 and cells[1] is not None else email
                if not name or len(name) > 200:
                    raise ValidationError(f"Строка {line}: имя должно содержать до 200 символов.")
                rows.append((email, name))
                if len(rows) > 1000:
                    raise ValidationError("В одном файле допускается до 1000 учеников.")
            if not rows:
                raise ValidationError("В файле нет почт учеников.")
            return rows
        except (BadZipFile, OSError, ValueError, KeyError, EOFError, IndexError, ParseError) as exc:
            raise ValidationError("Не удалось прочитать строки Excel.") from exc
    finally:
        book.close()
