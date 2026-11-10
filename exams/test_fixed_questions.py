from io import BytesIO
import struct
import zlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook
from docx import Document

from .fixed_questions import read_fixed_questions
from .ai import ManualReviewRequired, grade_open, suggest_marking_scheme
from .models import Exam, Question


class FixedQuestionImportTests(SimpleTestCase):
    def test_image_question_needs_teacher_authored_key(self):
        question = Question(exam=Exam(subject="Science"), slot=1, kind="open", grading_mode="ai",
                            points=2, stem="Name the gate shown.", image="exam_question_images/gate.png",
                            rubric=[{"criterion": "Correct and complete answer", "points": 2}])
        with patch("exams.ai.structured_response") as response:
            with self.assertRaisesMessage(ValidationError, "AI cannot see the question image"):
                suggest_marking_scheme(question)
            with self.assertRaises(ManualReviewRequired):
                grade_open(question, "AND")
            response.assert_not_called()

    def test_ai_scheme_must_match_existing_points(self):
        question = Question(exam=Exam(subject="Math"), slot=1, kind="open", grading_mode="ai",
                            points=3, stem="Explain the method.")
        with patch("exams.ai.structured_response", return_value={
            "rubric": [{"criterion": "Method", "points": 2}, {"criterion": "Answer", "points": 1}],
        }):
            self.assertEqual(suggest_marking_scheme(question), [
                {"criterion": "Method", "points": 2}, {"criterion": "Answer", "points": 1},
            ])
        self.assertEqual((question.stem, question.points), ("Explain the method.", 3))
        with patch("exams.ai.structured_response", return_value={
            "rubric": [{"criterion": "Method", "points": 2}],
        }):
            with self.assertRaisesMessage(ValidationError, "criterion points must add up to 3"):
                suggest_marking_scheme(question)

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

    def test_new_exam_uploads_questions_in_short_creation_form(self):
        add_url = "/admin/exams/exam/add/"
        page = self.client.get(add_url)
        self.assertContains(page, "Загрузить вопросы")
        self.assertNotContains(page, "Syllabus text")
        response = self.client.post(add_url, {
            "title": "Common test", "subject": "Math", "grade": "9", "duration_minutes": "30",
            "questions_file": SimpleUploadedFile("questions.txt", (
                "Question 1 — Fractions [2 marks]\nExplain how to add 1/2 and 1/4."
            ).encode()),
        })
        exam = Exam.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith(f"/admin/exams/exam/{exam.pk}/change/"))
        self.assertEqual(exam.mode, Exam.Mode.FIXED)
        self.assertEqual((exam.question_count, exam.total_points), (1, 2))
        self.assertEqual(exam.questions.get().stem,
                         "Question 1 — Fractions [2 marks]\nExplain how to add 1/2 and 1/4.")

    def test_generate_scheme_keeps_question_and_manual_scheme(self):
        exam = self.create_draft()
        generic = Question.objects.create(exam=exam, slot=1, kind="open", grading_mode="ai", points=3,
            stem="Explain the method.", rubric=[{"criterion": "Correct and complete answer", "points": 3}])
        manual = Question.objects.create(exam=exam, slot=2, kind="open", grading_mode="ai", points=2,
            stem="Give two reasons.", rubric=[{"criterion": "First reason", "points": 1},
                                                {"criterion": "Second reason", "points": 1}])
        page = self.client.get(f"/admin/exams/exam/{exam.pk}/change/")
        self.assertContains(page, "Сгенерировать схемы для текстовых вопросов (1)")
        url = f"/admin/exams/exam/{exam.pk}/questions/{generic.pk}/generate-scheme/"
        with patch("exams.admin.suggest_marking_scheme", return_value=[
            {"criterion": "Method", "points": 2}, {"criterion": "Answer", "points": 1},
        ]) as suggest:
            response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        suggest.assert_called_once()
        generic.refresh_from_db()
        self.assertEqual((generic.stem, generic.points), ("Explain the method.", 3))
        self.assertEqual(generic.rubric, [{"criterion": "Method", "points": 2},
                                          {"criterion": "Answer", "points": 1}])
        manual_url = f"/admin/exams/exam/{exam.pk}/questions/{manual.pk}/generate-scheme/"
        self.assertEqual(self.client.post(manual_url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code, 400)
        manual.refresh_from_db()
        self.assertEqual(manual.rubric[0]["criterion"], "First reason")
        exam.published = True
        exam.save(update_fields=["published"])
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_manual_import_and_publication_are_rejected(self):
        source = ("Question 1 — Diagram [2 marks]\nDescribe the circuit.\n"
                  "Question type: File upload or manually marked response")
        response = self.client.post("/admin/exams/exam/add/", {
            "title": "Common test", "subject": "Math", "grade": "9", "duration_minutes": "30",
            "questions_file": SimpleUploadedFile("questions.txt", source.encode()),
        })
        self.assertContains(response, "Удалите режим manual из файла")
        self.assertFalse(Exam.objects.exists())

        exam = self.create_draft()
        Question.objects.create(exam=exam, slot=1, kind="open", grading_mode="manual", points=2,
                                stem="Describe the circuit.",
                                rubric=[{"criterion": "Correct and complete answer", "points": 2}])
        publish = self.client.post(f"/admin/exams/exam/{exam.pk}/change/", {
            "title": exam.title, "subject": exam.subject, "grade": "9", "duration_minutes": "30",
            "published": "on", "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(exam.questions.get().pk), "questions-0-slot": "1",
            "questions-0-kind": "open", "questions-0-grading_mode": "manual",
            "questions-0-points": "2", "questions-0-stem": "Describe the circuit.",
        })
        self.assertContains(publish, "переключите вопросы из ручного режима на AI grading")
        exam.refresh_from_db()
        self.assertFalse(exam.published)

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
        book.active.append([1, "open", "ai", 3, "Explain the pictured circuit.", "yes"])
        stream = BytesIO()
        book.save(stream)
        response = self.client.post(f"/admin/exams/exam/{exam.pk}/upload-questions/", {
            "file": SimpleUploadedFile("questions.xlsx", stream.getvalue()),
        })
        self.assertEqual(response.status_code, 302)
        question = Question.objects.get(exam=exam)
        self.assertEqual(question.grading_mode, "ai")
        self.assertTrue(question.requires_image)
        self.assertEqual(question.points, 3)
        exam.refresh_from_db()
        self.assertEqual((exam.question_count, exam.total_points), (1, 3))
        publish_data = {
            "title": exam.title, "subject": exam.subject, "grade": "9",
            "duration_minutes": "30", "published": "on",
            "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(question.pk), "questions-0-slot": "1",
            "questions-0-kind": "open", "questions-0-grading_mode": "ai",
            "questions-0-points": "3", "questions-0-stem": question.stem,
            "questions-0-requires_image": "on",
        }
        publish = self.client.post(f"/admin/exams/exam/{exam.pk}/change/", publish_data)
        self.assertContains(publish, "Attach its image before publishing")
        exam.refresh_from_db()
        self.assertFalse(exam.published)

        question.image = "exam_question_images/gate.png"
        question.save(update_fields=["image"])
        publish = self.client.post(f"/admin/exams/exam/{exam.pk}/change/", publish_data)
        self.assertContains(publish, "вручную задайте критерии и образец ответа")
        exam.refresh_from_db()
        self.assertFalse(exam.published)

        publish_data.pop("published")
        publish_data["questions-0-criteria_text"] = "3: Correct circuit explanation"
        publish_data["questions-0-model_answer"] = "The pictured circuit uses an AND gate."
        self.assertEqual(self.client.post(f"/admin/exams/exam/{exam.pk}/change/", publish_data).status_code, 302)
        publish_data["published"] = "on"
        self.assertEqual(self.client.post(f"/admin/exams/exam/{exam.pk}/change/", publish_data).status_code, 302)
        exam.refresh_from_db()
        self.assertTrue(exam.published)
