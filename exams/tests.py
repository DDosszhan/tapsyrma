from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from docx import Document

from .models import Exam, ExamQuestionRule
from .syllabus import extract_syllabus_text


class SyllabusExtractionTests(SimpleTestCase):
    def test_docx_text_includes_paragraphs_and_tables(self):
        document = Document()
        document.add_paragraph("Требование 1")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Тема"
        table.cell(0, 1).text = "Алгебра"
        stream = BytesIO()
        document.save(stream)
        upload = SimpleUploadedFile("syllabus.docx", stream.getvalue())

        text = extract_syllabus_text(upload)

        self.assertIn("Требование 1", text)
        self.assertIn("Тема | Алгебра", text)


class ExamAdminTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_dir = TemporaryDirectory(prefix=".test-media-", dir=settings.BASE_DIR)
        assert Path(cls.media_dir.name).resolve().is_relative_to(settings.BASE_DIR.resolve())
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_dir.name))
        cls.addClassCleanup(cls.media_dir.cleanup)

    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "test-password")
        self.client.force_login(self.admin_user)

    @staticmethod
    def form_data(total_points):
        return {
            "title": "Ретейк по алгебре",
            "subject": "Математика",
            "grade": "9",
            "syllabus_file": SimpleUploadedFile("part.txt", "Линейные уравнения".encode()),
            "syllabus_text": "",
            "question_count": "5",
            "total_points": total_points,
            "question_rules-TOTAL_FORMS": "2",
            "question_rules-INITIAL_FORMS": "0",
            "question_rules-MIN_NUM_FORMS": "1",
            "question_rules-MAX_NUM_FORMS": "2",
            "question_rules-0-kind": ExamQuestionRule.Kind.MCQ,
            "question_rules-0-count": "4",
            "question_rules-0-points_each": "2.00",
            "question_rules-1-kind": ExamQuestionRule.Kind.OPEN,
            "question_rules-1-count": "1",
            "question_rules-1-points_each": "2.00",
        }

    def test_exam_requires_matching_blueprint_and_keeps_syllabus_private(self):
        url = "/admin/exams/exam/add/"
        invalid = self.client.post(url, self.form_data("9.00"))
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(Exam.objects.exists())

        saved = self.client.post(url, self.form_data("10.00"))
        self.assertEqual(saved.status_code, 302)
        exam = Exam.objects.get()
        self.assertEqual(exam.syllabus_text, "Линейные уравнения")
        self.assertEqual(exam.question_rules.count(), 2)
        self.assertEqual(exam.created_by, self.admin_user)

        self.client.logout()
        self.assertEqual(self.client.get(exam.syllabus_file.url).status_code, 302)
        self.client.force_login(self.admin_user)
        response = self.client.get(exam.syllabus_file.url)
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_open_questions_can_be_excluded_and_grade_is_limited(self):
        data = self.form_data("8.00")
        data.update({"title": "Только MCQ", "grade": "10", "question_count": "4", "question_rules-TOTAL_FORMS": "1"})
        for key in ("question_rules-1-kind", "question_rules-1-count", "question_rules-1-points_each"):
            del data[key]

        self.assertEqual(self.client.post("/admin/exams/exam/add/", data).status_code, 302)
        exam = Exam.objects.get()
        self.assertEqual(exam.grade, 10)
        self.assertEqual(list(exam.question_rules.values_list("kind", flat=True)), [ExamQuestionRule.Kind.MCQ])

        data = self.form_data("8.00")
        data.update({"grade": "11", "question_count": "4", "question_rules-TOTAL_FORMS": "1"})
        for key in ("question_rules-1-kind", "question_rules-1-count", "question_rules-1-points_each"):
            del data[key]
        self.assertEqual(self.client.post("/admin/exams/exam/add/", data).status_code, 200)
        self.assertEqual(Exam.objects.count(), 1)
