from pathlib import Path

from django.core.exceptions import ValidationError


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def validate_syllabus_file(upload):
    extension = Path(upload.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Поддерживаются только TXT, PDF и DOCX.")
    if upload.size == 0:
        raise ValidationError("Файл силабуса пустой.")
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError("Файл силабуса должен быть не больше 10 МБ.")
    return extension


def extract_syllabus_text(upload):
    extension = validate_syllabus_file(upload)
    try:
        upload.seek(0)
        if extension == ".txt":
            text = upload.read().decode("utf-8-sig")
        elif extension == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(upload)
            if len(reader.pages) > 100:
                raise ValidationError("Оставьте только нужный фрагмент PDF (до 100 страниц).")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            from docx import Document
            from docx.table import Table

            document = Document(upload)
            blocks = []
            for block in document.iter_inner_content():
                if isinstance(block, Table):
                    blocks.extend(" | ".join(cell.text for cell in row.cells) for row in block.rows)
                else:
                    blocks.append(block.text)
            text = "\n".join(blocks)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("Не удалось прочитать файл. Проверьте формат или вставьте текст вручную.") from exc
    finally:
        upload.seek(0)

    text = text.strip()
    if not text:
        raise ValidationError("В файле нет извлекаемого текста. Для скана вставьте текст после OCR вручную.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError("Оставьте только нужный фрагмент силабуса (до 100 000 символов).")
    return text
