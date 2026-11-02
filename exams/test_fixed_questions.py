from io import BytesIO
import struct
import zlib

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook
from docx import Document

from .fixed_questions import read_fixed_questions
from .models import Exam, Question


class FixedQuestionImportTests(SimpleTestCase):
    def test_text_prompt_keeps_questions_and_removes_teacher_metadata(self):
        source = ("Question 1 — Logic Gates [3 marks]\nExplain the circuit.\n"
                  "Question type: File upload or manually marked response\n"
                  "IMPORTANT: Insert the supplied image named logic_gates_insane.png directly above this question.\n"
                  "Question 2 — Binary [2 marks]\nConvert 3 to binary.\n"
                  "Assessment configuration\nCreate all six questions as written.")
        rows = read_fixed_questions(SimpleUploadedFile("questions.txt", source.encode()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["stem"], "Question 1 — Logic Gates [3 marks]\nExplain the circuit.")
        self.assertEqual(rows[0]["grading_mode"], "manual")
        self.assertTrue(rows[0]["requires_image"])
        self.assertEqual(rows[1]["stem"], "Question 2 — Binary [2 marks]\nConvert 3 to binary.")
        self.assertEqual(rows[1]["rubric"], [{"criterion": "Correct and complete answer", "points": 2}])

    def test_embedded_docx_image_is_rejected_before_it_can_be_lost(self):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
               + chunk(b"IEND", b""))
        document = Document()
        document.add_paragraph("Question 1 — Circuit [1 mark]")
        document.add_paragraph("Name the gate in the picture.")
        document.add_picture(BytesIO(png))
        stream = BytesIO()
        document.save(stream)
        with self.assertRaisesMessage(ValidationError, "Embedded images cannot be assigned"):
            read_fixed_questions(SimpleUploadedFile("questions.docx", stream.getvalue()))


class FixedQuestionAdminTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "test-password")
        self.client.force_login(self.user)

    def create_draft(self):
        response = self.client.post("/admin/exams/exam/add/", {
            "title": "Same questions", "subject": "Math", "grade": "9", "mode": "fixed",
            "duration_minutes": "30", "questions-TOTAL_FORMS": "0",
            "questions-INITIAL_FORMS": "0", "questions-MIN_NUM_FORMS": "0",
            "questions-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(response.status_code, 302)
        exam = Exam.objects.get()
        self.assertEqual((exam.question_count, exam.total_points), (0, 0))
        return exam

    def test_manual_question_can_be_published_without_key_or_criteria(self):
        exam = self.create_draft()
        url = f"/admin/exams/exam/{exam.pk}/change/"
        data = {
            "title": exam.title, "subject": exam.subject, "grade": "9", "duration_minutes": "30",
            "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "0",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-slot": "1", "questions-0-kind": "open",
            "questions-0-grading_mode": "ai", "questions-0-points": "3",
            "questions-0-stem": "Explain why 2 + 1 = 3.",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        question = exam.questions.get()
        self.assertEqual(question.model_answer, "")
        self.assertEqual(question.rubric, [{"criterion": "Correct and complete answer", "points": 3}])
        exam.refresh_from_db()
        self.assertEqual((exam.question_count, exam.total_points), (1, 3))
        data.update({"published": "on", "questions-INITIAL_FORMS": "1", "questions-0-id": str(question.pk)})
        self.assertEqual(self.client.post(url, data).status_code, 302)
        exam.refresh_from_db()
        self.assertTrue(exam.published)

    def test_xlsx_upload_and_required_image_block_publication(self):
        exam = self.create_draft()
        book = Workbook()
        book.active.append(["number", "kind", "grading_mode", "points", "question", "requires_image"])
        book.active.append([1, "open", "manual", 3, "Explain the pictured circuit.", "yes"])
        stream = BytesIO()
        book.save(stream)
        response = self.client.post(f"/admin/exams/exam/{exam.pk}/upload-questions/", {
            "file": SimpleUploadedFile("questions.xlsx", stream.getvalue()),
        })
        self.assertEqual(response.status_code, 302)
        question = Question.objects.get(exam=exam)
        self.assertEqual(question.grading_mode, "manual")
        self.assertTrue(question.requires_image)
        self.assertEqual(question.points, 3)
        exam.refresh_from_db()
        self.assertEqual((exam.question_count, exam.total_points), (1, 3))
        publish = self.client.post(f"/admin/exams/exam/{exam.pk}/change/", {
            "title": exam.title, "subject": exam.subject, "grade": "9",
            "duration_minutes": "30", "published": "on",
            "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(question.pk), "questions-0-slot": "1",
            "questions-0-kind": "open", "questions-0-grading_mode": "manual",
            "questions-0-points": "3", "questions-0-stem": question.stem,
            "questions-0-requires_image": "on",
        })
        self.assertContains(publish, "Attach its image before publishing")
        exam.refresh_from_db()
        self.assertFalse(exam.published)
