from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
import hashlib
import hmac
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from docx import Document

from .ai import generate_round, grade_open, validate_questions
from .grading import grade_attempt
from .models import Attempt, Exam, ExamQuestionRule, Invitation, Question, Student
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


class ExamFlowTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        self.exam = Exam.objects.create(title="Алгебра", subject="Математика", grade=9,
                                        syllabus_text="Линейные уравнения", question_count=2, total_points=4,
                                        created_by=self.teacher)
        ExamQuestionRule.objects.create(exam=self.exam, kind="mcq", count=1, points_each=1)
        ExamQuestionRule.objects.create(exam=self.exam, kind="open", count=1, points_each=3)
        self.generated = {"questions": [
            {"slot": 1, "kind": "mcq", "points": 1, "subtopic": "Уравнения", "stem": "Найдите x: x+2=3 [1]",
             "options": ["0", "1", "2", "3"], "correct_option": 1,
             "rubric": [{"criterion": "Верный ответ", "points": 1}], "model_answer": "1",
             "subtopic_explanation": "Переносим 2.", "answer_explanation": "x=1."},
            {"slot": 2, "kind": "open", "points": 3, "subtopic": "Уравнения", "stem": "Решите 2x+4=8. Покажите шаги. [3]",
             "options": [], "correct_option": 0,
             "rubric": [{"criterion": "Вычесть 4", "points": 1}, {"criterion": "Разделить на 2", "points": 1},
                        {"criterion": "Ответ x=2", "points": 1}], "model_answer": "2x=4, x=2",
             "subtopic_explanation": "Изолируйте x.", "answer_explanation": "Сначала вычесть 4, затем разделить на 2."},
        ]}

    def test_generation_validation_and_full_student_flow(self):
        invalid = {"questions": [dict(self.generated["questions"][0]), dict(self.generated["questions"][1])]}
        invalid["questions"][1]["rubric"] = [{"criterion": "Только ответ", "points": 1}]
        with self.assertRaisesMessage(Exception, "критерии"):
            validate_questions(self.exam, invalid)
        for q in validate_questions(self.exam, self.generated):
            Question.objects.create(exam=self.exam, **q)
        self.exam.published = True
        self.exam.save()
        student = Student.objects.create(name="Ученик", grade=9, external_id="S-1")
        code = "ABCD2345"
        invitation = Invitation.objects.create(exam=self.exam, student=student,
            code_hash=hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
        self.assertEqual(self.client.post("/", {"code": code}).status_code, 302)
        self.assertContains(self.client.get("/exam/"), "Ученик")
        self.assertEqual(self.client.post("/exam/").status_code, 302)
        attempt = Attempt.objects.get(invitation=invitation)
        self.assertEqual(self.client.post("/exam/").status_code, 200)
        page = self.client.get("/exam/")
        self.assertContains(page, "Решите 2x")
        self.assertNotContains(page, "Сначала вычесть 4")
        self.assertNotContains(page, "Верный ответ")
        q1, q2 = Question.objects.order_by("slot")
        answers = {f"q_{q1.pk}": "1", f"q_{q2.pk}": "2x=4, x=2"}
        self.assertEqual(self.client.post("/exam/save/", answers).status_code, 200)
        self.assertEqual(self.client.post("/exam/submit/", answers).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.PENDING)
        with patch("exams.grading.grade_open", return_value={"score": 2, "criteria": [1, 1, 0], "explanation": "Два шага верны."}):
            grade_attempt(attempt)
        result = self.client.get("/exam/result/")
        self.assertContains(result, "3 / 4")
        self.assertContains(result, "Вычесть 4")
        self.assertContains(result, "Изолируйте x")
        self.assertEqual(self.client.get("/exam/").status_code, 302)
        self.assertEqual(Attempt.objects.count(), 1)

    def test_admin_generation_and_code_issue(self):
        self.client.force_login(self.teacher)
        with patch("exams.admin.generate_round", return_value=[Question(exam=self.exam, **q) for q in self.generated["questions"]]):
            response = self.client.post(f"/admin/exams/exam/{self.exam.pk}/generate/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.exam.questions.count(), 2)
        response = self.client.get(f"/admin/exams/exam/{self.exam.pk}/change/")
        self.assertContains(response, "Вычесть 4")
        draft = self.exam.questions.order_by("slot").first()
        self.assertEqual(self.client.post(f"/admin/exams/question/{draft.pk}/delete/", {"post": "yes"}).status_code, 302)
        self.assertEqual(self.exam.questions.count(), 1)
        Question.objects.create(exam=self.exam, **self.generated["questions"][0])
        student = Student.objects.create(name="Ученик", grade=9, external_id="S-1")
        publish = self.client.post(f"/admin/exams/exam/{self.exam.pk}/change/", {
            "title": self.exam.title, "published": "on",
            "questions-TOTAL_FORMS": "2", "questions-INITIAL_FORMS": "2",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            **{f"questions-{i}-id": q.pk for i, q in enumerate(self.exam.questions.all())},
        })
        self.assertEqual(publish.status_code, 302)
        self.exam.refresh_from_db()
        self.assertTrue(self.exam.published)
        published_question = self.exam.questions.first()
        self.assertIn(self.client.get(f"/admin/exams/question/{published_question.pk}/delete/").status_code, [403, 404])
        response = self.client.post("/admin/exams/invitation/add/", {
            "exam": self.exam.pk, "student": student.pk,
            "expires_at_0": (timezone.now() + timedelta(days=1)).date().isoformat(),
            "expires_at_1": "12:00:00",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invitation.objects.count(), 1)
        self.assertTrue(Invitation.objects.get().code_hash)

    def test_structured_api_and_rejects_grades_above_rubric(self):
        question = Question.objects.create(exam=self.exam, **self.generated["questions"][1])
        api_body = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"scores": [1, 1, 0], "explanation": "Два шага верны."})}
        ]}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "OPENAI_MODEL": "gpt-4o-mini"}), \
             patch("urllib.request.urlopen", return_value=BytesIO(json.dumps(api_body).encode())) as post:
            grade = grade_open(question, "2x=4, x=2")
        self.assertEqual(grade["score"], 2)
        payload = json.loads(post.call_args.args[0].data)
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        with patch("exams.ai.structured_response", return_value={"scores": [2, 1, 0], "explanation": ""}):
            with self.assertRaisesMessage(Exception, "вне утвержденных критериев"):
                grade_open(question, "ответ")

    def test_generation_uses_igcse_for_nine_and_as_for_ten(self):
        with patch("exams.ai.structured_response", return_value=self.generated) as response:
            self.assertEqual(len(generate_round(self.exam)), 2)
        self.assertIn("IGCSE", response.call_args.args[2])
        self.exam.grade = 10
        with patch("exams.ai.structured_response", return_value=self.generated) as response:
            self.assertEqual(len(generate_round(self.exam)), 2)
        self.assertIn("AS Level", response.call_args.args[2])
