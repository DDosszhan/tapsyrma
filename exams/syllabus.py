from pathlib import Path

from django.core.exceptions import ValidationError


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def validate_syllabus_file(upload):
    extension = Path(upload.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Only TXT, PDF and DOCX files are supported.")
    if upload.size == 0:
        raise ValidationError("The syllabus file is empty.")
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError("The syllabus file must be 10 MB or smaller.")
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
                raise ValidationError("Keep only the relevant PDF excerpt (up to 100 pages).")
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
        raise ValidationError("Could not read the file. Check its format or paste the text manually.") from exc
    finally:
        upload.seek(0)

    text = text.strip()
    if not text:
        raise ValidationError("The file has no extractable text. For scans, paste OCR text manually.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValidationError("Keep only the relevant syllabus excerpt (up to 100,000 characters).")
    return text
