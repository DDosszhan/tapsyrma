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
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from docx import Document
from openpyxl import Workbook

from .ai import generate_round, grade_open, validate_questions
from .grading import grade_attempt
from .management.commands.grade_attempts import expire_attempts
from .models import Attempt, Exam, ExamQuestionRule, Invitation, Question, SharedCode, Student
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
            "duration_minutes": "75",
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
        bad_duration = self.form_data("10.00")
        bad_duration["duration_minutes"] = "0"
        self.assertEqual(self.client.post(url, bad_duration).status_code, 200)
        self.assertFalse(Exam.objects.exists())

        saved = self.client.post(url, self.form_data("10.00"))
        self.assertEqual(saved.status_code, 302)
        exam = Exam.objects.get()
        self.assertEqual(exam.syllabus_text, "Линейные уравнения")
        self.assertEqual(exam.question_rules.count(), 2)
        self.assertEqual(exam.duration_minutes, 75)
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


class StudentAccountAdminTests(TestCase):
    def setUp(self):
        admin_user = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "test-password")
        self.client.force_login(admin_user)

    @staticmethod
    def workbook(rows):
        book = Workbook()
        sheet = book.active
        for row in rows:
            sheet.append(row)
        stream = BytesIO()
        book.save(stream)
        return SimpleUploadedFile("students.xlsx", stream.getvalue(),
                                  content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_admin_creates_student_login_with_hashed_initial_password(self):
        response = self.client.post("/admin/exams/student/add/", {
            "email": "Pupil@Example.invalid", "name": "Ученик", "grade": "9", "_save": "Сохранить",
        })
        self.assertEqual(response.status_code, 302)
        student = Student.objects.select_related("user").get()
        self.assertEqual(student.user.username, "pupil@example.invalid")
        self.assertTrue(student.user.check_password(settings.STUDENT_INITIAL_PASSWORD))
        self.assertNotEqual(student.user.password, settings.STUDENT_INITIAL_PASSWORD)
        self.assertTrue(student.password_change_required)

    def test_excel_import_is_atomic_and_safe_to_repeat(self):
        url = "/admin/exams/student/import/"
        upload = self.workbook([["email", "name"], ["first@example.invalid", "Первый"],
                                ["second@example.invalid", "Второй"]])
        self.assertEqual(self.client.post(url, {"grade": "10", "file": upload}).status_code, 302)
        self.assertEqual(Student.objects.count(), 2)
        self.assertEqual(set(Student.objects.values_list("grade", flat=True)), {10})
        self.assertEqual(self.client.post(url, {"grade": "10", "file": self.workbook([
            ["email", "name"], ["first@example.invalid", "Первый"],
            ["second@example.invalid", "Второй"],
        ])}).status_code, 302)
        self.assertEqual(Student.objects.count(), 2)

        response = self.client.post(url, {"grade": "9", "file": self.workbook([
            ["email"], ["third@example.invalid"], ["invalid-email"],
        ])})
        self.assertContains(response, "некорректная почта")
        self.assertFalse(get_user_model().objects.filter(username="third@example.invalid").exists())

        response = self.client.post(url, {"grade": "9", "file": self.workbook([
            ["email"], ["fourth@example.invalid"], ["admin@example.invalid"],
        ])})
        self.assertContains(response, "уже занята")
        self.assertFalse(get_user_model().objects.filter(username="fourth@example.invalid").exists())


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
        self.exam.published = True
        self.exam.save()
        email = "student@example.invalid"
        student_user = get_user_model().objects.create_user(email, email, settings.STUDENT_INITIAL_PASSWORD)
        student = Student.objects.create(user=student_user, name="Ученик", grade=9, external_id="S-1")
        code = "ABCD2345"
        invitation = Invitation.objects.create(exam=self.exam, student=student,
            code_hash=hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
        self.assertContains(self.client.get("/"), "Алгебра")
        self.assertNotContains(self.client.get("/"), "Решите 2x")
        self.assertEqual(self.client.post("/", {"code": code}).status_code, 302)
        self.assertEqual(self.client.get("/exam/").status_code, 302)
        with patch("exams.management.commands.prepare_invitations.generate_round",
                   return_value=[Question(exam=self.exam, **q) for q in validate_questions(self.exam, self.generated)]):
            call_command("prepare_invitations", once=True)
        invitation.refresh_from_db()
        self.assertEqual(invitation.variant_status, Invitation.VariantStatus.READY)
        self.assertRedirects(self.client.post("/login/", {"username": email, "password": settings.STUDENT_INITIAL_PASSWORD}), "/password/change/")
        self.assertRedirects(self.client.get("/"), "/password/change/")
        self.assertRedirects(self.client.post("/password/change/", {
            "old_password": settings.STUDENT_INITIAL_PASSWORD, "new_password1": "Strong-New-Password-2026!",
            "new_password2": "Strong-New-Password-2026!",
        }), "/")
        student.refresh_from_db()
        self.assertFalse(student.password_change_required)
        self.client.logout()
        self.assertRedirects(self.client.post("/login/", {"username": email.upper(),
                                                          "password": "Strong-New-Password-2026!"}), "/")
        self.assertEqual(self.client.post("/", {"code": code}).status_code, 302)
        self.assertContains(self.client.get("/exam/"), "Ученик")
        self.assertEqual(self.client.post("/exam/").status_code, 302)
        attempt = Attempt.objects.get(invitation=invitation)
        self.assertEqual(self.client.post("/exam/").status_code, 200)
        page = self.client.get("/exam/")
        self.assertContains(page, "Решите 2x")
        self.assertNotContains(page, "Сначала вычесть 4")
        self.assertNotContains(page, "Верный ответ")
        q1, q2 = invitation.questions.order_by("slot")
        answers = {f"q_{q1.pk}": "1", f"q_{q2.pk}": "2x=4, x=2"}
        self.assertEqual(self.client.post("/exam/save/", answers).status_code, 200)
        self.assertEqual(self.client.post("/exam/submit/", answers).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.PENDING)
        pending_result = self.client.get("/exam/result/")
        self.assertContains(pending_result, "Сначала вычесть 4")
        self.assertContains(pending_result, "2x=4, x=2")
        with patch("exams.grading.grade_open", return_value={"score": 2, "criteria": [1, 1, 0], "explanation": "Два шага верны."}):
            grade_attempt(attempt)
        result = self.client.get("/exam/result/")
        self.assertContains(result, "3 / 4")
        self.assertContains(result, "Вычесть 4")
        self.assertContains(result, "Изолируйте x")
        self.assertContains(self.client.get("/history/"), "Алгебра")
        self.assertContains(self.client.get(f"/exam/result/{attempt.pk}/"), "3 / 4")
        self.client.logout()
        self.client.login(username=email, password="Strong-New-Password-2026!")
        self.assertContains(self.client.get(f"/exam/result/{attempt.pk}/"), "Изолируйте x")
        self.assertEqual(self.client.post("/", {"code": code}).status_code, 302)
        self.assertEqual(self.client.get("/exam/").status_code, 302)
        self.assertEqual(Attempt.objects.count(), 1)

        another_email = "another@example.invalid"
        another_user = get_user_model().objects.create_user(another_email, another_email, "Another-Password-2026!")
        Student.objects.create(user=another_user, name="Другой", grade=9, password_change_required=False)
        self.client.logout()
        self.client.login(username=another_email, password="Another-Password-2026!")
        self.assertContains(self.client.post("/", {"code": code}), "Код недействителен")
        self.assertNotContains(self.client.get("/history/"), "Алгебра")
        self.assertEqual(self.client.get(f"/exam/result/{attempt.pk}/").status_code, 404)

    def test_duration_starts_with_attempt_and_expiry_keeps_saved_answers(self):
        self.exam.published = True
        self.exam.duration_minutes = 2
        self.exam.save()
        user = get_user_model().objects.create_user("student@example.invalid", "student@example.invalid", "password")
        student = Student.objects.create(user=user, name="Ученик", grade=9, password_change_required=False)
        invitation = Invitation.objects.create(
            exam=self.exam, student=student, code_hash="timer-test", variant_status=Invitation.VariantStatus.READY,
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
        questions = [Question.objects.create(exam=self.exam, invitation=invitation, **q)
                     for q in self.generated["questions"]]
        self.client.force_login(user)
        session = self.client.session
        session["invitation_id"] = invitation.pk
        session.save()

        self.assertRedirects(self.client.post("/exam/"), "/exam/")
        attempt = Attempt.objects.get(invitation=invitation)
        self.assertAlmostEqual((attempt.deadline_at - attempt.started_at).total_seconds(), 120, delta=2)
        self.assertContains(self.client.get("/exam/"), 'id="countdown"')
        answer_key = f"q_{questions[0].pk}"
        self.assertEqual(self.client.post("/exam/save/", {answer_key: "1"}).status_code, 200)
        attempt.deadline_at = timezone.now() - timedelta(seconds=1)
        attempt.save(update_fields=["deadline_at"])
        self.assertEqual(self.client.post("/exam/save/", {answer_key: "2"}).status_code, 409)
        self.assertRedirects(self.client.post("/exam/submit/", {answer_key: "2"}), "/exam/result/")
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.PENDING)
        self.assertEqual(attempt.answers[str(questions[0].pk)], "1")
        self.assertIsNotNone(attempt.submitted_at)

        def extra_attempt(deadline):
            other = Invitation.objects.create(
                exam=self.exam, student=student, code_hash=f"timer-{Invitation.objects.count()}",
                expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
            return Attempt.objects.create(invitation=other, deadline_at=deadline)

        expired = extra_attempt(timezone.now() - timedelta(seconds=1))
        session = self.client.session
        session["invitation_id"] = expired.invitation_id
        session.save()
        self.assertRedirects(self.client.get("/exam/"), "/exam/result/")
        expired.refresh_from_db()
        self.assertEqual(expired.status, Attempt.Status.PENDING)
        background = extra_attempt(timezone.now() - timedelta(seconds=1))
        legacy = extra_attempt(None)
        self.assertEqual(expire_attempts(), 1)
        background.refresh_from_db()
        legacy.refresh_from_db()
        self.assertEqual(background.status, Attempt.Status.PENDING)
        self.assertEqual(legacy.status, Attempt.Status.ACTIVE)

    def test_admin_publishes_without_bank_and_issues_code(self):
        self.client.force_login(self.teacher)
        student_user = get_user_model().objects.create_user("student@example.invalid", "student@example.invalid",
                                                            settings.STUDENT_INITIAL_PASSWORD)
        student = Student.objects.create(user=student_user, name="Ученик", grade=9, external_id="S-1")
        publish = self.client.post(f"/admin/exams/exam/{self.exam.pk}/change/", {
            "title": self.exam.title, "subject": self.exam.subject, "grade": "9",
            "syllabus_text": self.exam.syllabus_text, "question_count": "2", "total_points": "4.00",
            "duration_minutes": "60",
            "published": "on", "question_rules-TOTAL_FORMS": "2", "question_rules-INITIAL_FORMS": "2",
            "question_rules-MIN_NUM_FORMS": "1", "question_rules-MAX_NUM_FORMS": "2",
            **{f"question_rules-{i}-{key}": value
               for i, rule in enumerate(self.exam.question_rules.order_by("id"))
               for key, value in {"id": rule.pk, "kind": rule.kind, "count": rule.count,
                                  "points_each": str(rule.points_each)}.items()},
        })
        self.assertEqual(publish.status_code, 302, publish.context["adminform"].form.errors if publish.context else "")
        self.exam.refresh_from_db()
        self.assertTrue(self.exam.published)
        response = self.client.post("/admin/exams/invitation/add/", {
            "exam": self.exam.pk, "student": student.pk,
            "expires_at_0": (timezone.now() + timedelta(days=1)).date().isoformat(),
            "expires_at_1": "12:00:00",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invitation.objects.count(), 1)
        self.assertTrue(Invitation.objects.get().code_hash)
        self.assertEqual(Invitation.objects.get().variant_status, Invitation.VariantStatus.PENDING)

    def test_each_code_gets_its_own_questions(self):
        self.exam.published = True
        self.exam.save()
        for number in (1, 2):
            email = f"student{number}@example.invalid"
            user = get_user_model().objects.create_user(email, email, "test-password")
            student = Student.objects.create(user=user, name=f"Ученик {number}", grade=9)
            Invitation.objects.create(exam=self.exam, student=student, code_hash=f"hash-{number}",
                                      expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
        generated = []
        for number in (1, 2):
            rows = [dict(q, stem=q["stem"] if q["slot"] == 1 else f"{q['stem']} Вариант {number}")
                    for q in self.generated["questions"]]
            generated.append([Question(exam=self.exam, **q) for q in rows])
        with patch("exams.management.commands.prepare_invitations.generate_round", side_effect=generated):
            call_command("prepare_invitations", once=True)
            call_command("prepare_invitations", once=True)
        first, second = Invitation.objects.order_by("id")
        self.assertEqual(first.variant_status, Invitation.VariantStatus.READY)
        self.assertEqual(second.variant_status, Invitation.VariantStatus.READY)
        self.assertEqual(first.questions.count(), 2)
        self.assertEqual(second.questions.count(), 2)
        self.assertFalse(set(first.questions.values_list("id", flat=True)) & set(second.questions.values_list("id", flat=True)))
        self.assertEqual(first.questions.get(slot=1).stem, second.questions.get(slot=1).stem)

    def test_identical_whole_variant_is_rejected(self):
        self.exam.published = True
        self.exam.save()
        for number in (1, 2):
            email = f"student{number}@example.invalid"
            user = get_user_model().objects.create_user(email, email, "test-password")
            student = Student.objects.create(user=user, name=f"Ученик {number}", grade=9)
            Invitation.objects.create(exam=self.exam, student=student, code_hash=f"hash-{number}",
                                      expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
        with patch("exams.management.commands.prepare_invitations.generate_round",
                   side_effect=lambda exam, variation_id: [Question(exam=exam, **q) for q in self.generated["questions"]]) as generate:
            call_command("prepare_invitations", once=True)
            call_command("prepare_invitations", once=True)
        statuses = list(Invitation.objects.order_by("id").values_list("variant_status", flat=True))
        self.assertEqual(statuses, [Invitation.VariantStatus.READY, Invitation.VariantStatus.ERROR])
        self.assertEqual(generate.call_count, 4)

    def test_pending_code_cannot_start_and_catalog_hides_drafts(self):
        draft = Exam.objects.create(title="Черновик", subject="Математика", grade=9,
                                    syllabus_text="Силабус", question_count=1, total_points=1, created_by=self.teacher)
        self.exam.published = True
        self.exam.save()
        catalog = self.client.get("/")
        self.assertContains(catalog, "Алгебра")
        self.assertNotContains(catalog, draft.title)
        user = get_user_model().objects.create_user("student@example.invalid", "student@example.invalid", "password")
        student = Student.objects.create(user=user, name="Ученик", grade=9, password_change_required=False)
        invitation = Invitation.objects.create(exam=self.exam, student=student, code_hash="unused-hash",
                                                expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher)
        self.client.force_login(user)
        session = self.client.session
        session["invitation_id"] = invitation.pk
        session.save()
        self.assertContains(self.client.get("/exam/"), "вариант готовится")
        self.assertContains(self.client.post("/exam/"), "Вариант еще не готов")
        self.assertFalse(Attempt.objects.exists())

    def test_shared_code_allows_one_exam_choice_per_student(self):
        self.exam.published = True
        self.exam.save()
        other = Exam.objects.create(title="Геометрия", subject="Математика", grade=9, published=True,
                                    syllabus_text="Треугольники", question_count=1, total_points=1, created_by=self.teacher)
        Exam.objects.create(title="AS физика", subject="Физика", grade=10, published=True,
                            syllabus_text="Движение", question_count=1, total_points=1, created_by=self.teacher)
        code = "SHARED23"
        shared = SharedCode.objects.create(
            code_hash=hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=2), created_by=self.teacher)
        users = []
        for number in (1, 2):
            email = f"student{number}@example.invalid"
            user = get_user_model().objects.create_user(email, email, "test-password")
            Student.objects.create(user=user, name=f"Ученик {number}", grade=9, password_change_required=False)
            users.append(user)

        self.client.force_login(users[0])
        self.assertRedirects(self.client.post("/", {"code": code}), "/choose/")
        choices = self.client.get("/choose/")
        self.assertContains(choices, "Алгебра")
        self.assertContains(choices, "Геометрия")
        self.assertNotContains(choices, "AS физика")
        self.assertEqual(self.client.post("/choose/", {"exam_id": "bad"}).status_code, 400)
        self.assertContains(self.client.post("/choose/", {"exam_id": str(Exam.objects.get(title="AS физика").pk)}),
                            "Экзамен или код больше недоступен")
        self.assertRedirects(self.client.post("/choose/", {"exam_id": self.exam.pk}), "/exam/")
        first = Invitation.objects.get(shared_code=shared, student__user=users[0])
        self.assertEqual(first.exam, self.exam)
        self.assertEqual(first.variant_status, Invitation.VariantStatus.PENDING)
        self.assertRedirects(self.client.post("/choose/", {"exam_id": other.pk}), "/exam/")
        self.assertEqual(Invitation.objects.filter(shared_code=shared, student__user=users[0]).count(), 1)
        with patch("exams.management.commands.prepare_invitations.generate_round",
                   return_value=[Question(exam=self.exam, **q) for q in self.generated["questions"]]):
            call_command("prepare_invitations", once=True)
        self.assertRedirects(self.client.post("/exam/"), "/exam/")
        self.assertEqual(Attempt.objects.get(invitation=first).question_ids, list(first.questions.values_list("id", flat=True)))

        self.client.force_login(users[1])
        shared.expires_at = timezone.now() + timedelta(minutes=5)
        shared.save(update_fields=["expires_at"])
        self.assertRedirects(self.client.post("/", {"code": code}), "/choose/")
        self.assertRedirects(self.client.post("/choose/", {"exam_id": other.pk}), "/exam/")
        self.assertEqual(Invitation.objects.filter(shared_code=shared).count(), 2)
        self.assertGreater(Invitation.objects.get(shared_code=shared, student__user=users[1]).expires_at,
                           timezone.now() + timedelta(minutes=59))
        shared.is_active = False
        shared.save(update_fields=["is_active"])
        self.assertRedirects(self.client.post("/", {"code": code}), "/exam/")

    def test_admin_creates_common_code_without_storing_plaintext(self):
        self.client.force_login(self.teacher)
        code = "SHARED23"
        digest = hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()
        with patch("exams.admin.new_code", return_value=(code, digest)):
            response = self.client.post("/admin/exams/sharedcode/add/", {
                "expires_at_0": (timezone.now() + timedelta(days=2)).date().isoformat(),
                "expires_at_1": "12:00:00", "is_active": "on",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SharedCode.objects.get().code_hash, digest)
        self.assertContains(self.client.get("/admin/exams/sharedcode/"), code)

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
        mismatch = {"questions": [dict(self.generated["questions"][0], model_answer="wrong"),
                                  dict(self.generated["questions"][1])]}
        with patch("exams.ai.structured_response", return_value=mismatch) as response:
            questions = generate_round(self.exam, variation_id="test-1")
            self.assertEqual(len(questions), 2)
            self.assertEqual(questions[0].model_answer, "1")
        self.assertIn("IGCSE", response.call_args.args[2])
        self.assertIn("test-1", response.call_args.args[3])
        self.exam.grade = 10
        with patch("exams.ai.structured_response", return_value=self.generated) as response:
            self.assertEqual(len(generate_round(self.exam)), 2)
        self.assertIn("AS Level", response.call_args.args[2])
