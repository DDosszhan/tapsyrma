from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from .fixed_questions import normalize_question, read_fixed_questions, validate_stored_questions
from .models import Exam, Question


class StructuredImportTests(SimpleTestCase):
    def test_cloze_has_ordered_blanks_and_one_point_each(self):
        row = normalize_question({
            "slot": 1, "kind": "cloze", "points": 2,
            "stem": "The [[1]] stores [[2]].",
            "answer_items": "CPU|processor\ninstructions",
        })
        self.assertEqual(row["interaction"], {"answers": [["CPU", "processor"], ["instructions"]]})
        self.assertEqual(row["options"], [])
        self.assertEqual([item["points"] for item in row["rubric"]], [1, 1])
        self.assertEqual(row["model_answer"], "1. CPU\n2. instructions")
        for stem, items in (("The [[2]] stores [[1]].", "CPU\ninstructions"),
                            ("The [[1]] stores [[2]].", "CPU"),
                            ("The [[1]] stores [[2]].", "CPU|cpu\ninstructions"),
                            ("The [[1]] stores [[2]].", "X" * 121 + "\ninstructions")):
            with self.assertRaises(ValidationError):
                normalize_question({"slot": 1, "kind": "cloze", "points": 2,
                                    "stem": stem, "answer_items": items})

    def test_matching_has_shuffled_right_column_and_unique_pairs(self):
        data = {"slot": 1, "kind": "matching", "points": 3, "stem": "Match each term.",
                "answer_items": "CPU => processor\nRAM => memory\nSSD => storage"}
        with patch("exams.fixed_questions.SystemRandom") as randomizer:
            randomizer.return_value.shuffle.side_effect = lambda values: values.reverse()
            row = normalize_question(data)
            self.assertEqual(row["options"], ["storage", "memory", "processor"])
            randomizer.return_value.shuffle.assert_called_once()
            same = normalize_question({**data, "options": row["options"]})
            self.assertEqual(same["options"], row["options"])
            randomizer.return_value.shuffle.assert_called_once()
        self.assertEqual(row["interaction"], {"left": ["CPU", "RAM", "SSD"], "answers": [2, 1, 0]})
        self.assertEqual([item["points"] for item in row["rubric"]], [1, 1, 1])
        for items in ("CPU => processor", "CPU => processor\nRAM => processor",
                      "CPU => processor\nCPU => memory", "CPU - processor\nRAM => memory"):
            with self.assertRaises(ValidationError):
                normalize_question({"slot": 1, "kind": "matching", "points": 2,
                                    "stem": "Match each term.", "answer_items": items})

    def test_multiselect_requires_two_or_three_keys_and_four_options(self):
        data = {"slot": 1, "kind": "multiselect", "points": 2, "stem": "Choose two.",
                "options": ["CPU", "RAM", "SSD", "GPU"], "answer_items": "3,1"}
        row = normalize_question(data)
        self.assertEqual(row["interaction"], {"answers": [0, 2]})
        self.assertEqual(row["rubric"], [{"criterion": "Correct choices minus incorrect choices (minimum 0)", "points": 2}])
        self.assertEqual(row["model_answer"], "1. CPU\n3. SSD")
        for change in ({"answer_items": "1"}, {"answer_items": "1,2,3,4"},
                       {"answer_items": "1,1"}, {"points": 3},
                       {"options": ["CPU", "cpu", "SSD", "GPU"]}):
            with self.assertRaises(ValidationError):
                normalize_question({**data, **change})

    def test_ordering_shuffles_once_and_keeps_saved_permutation(self):
        data = {"slot": 1, "kind": "ordering", "points": 3, "stem": "Order the steps.",
                "answer_items": "Start\nProcess\nFinish"}
        with patch("exams.fixed_questions.SystemRandom") as randomizer:
            randomizer.return_value.shuffle.side_effect = lambda values: values.reverse()
            row = normalize_question(data)
            self.assertEqual(row["options"], ["Finish", "Process", "Start"])
            self.assertEqual(row["interaction"], {"answers": [2, 1, 0]})
            same = normalize_question({**data, "options_hint": row["options"]})
            self.assertEqual(same["options"], row["options"])
            randomizer.return_value.shuffle.assert_called_once()
        self.assertEqual([item["points"] for item in row["rubric"]], [1, 1, 1])
        for change in ({"answer_items": "Start\nStart\nFinish"},
                       {"answer_items": "Start\nFinish"},
                       {"points": 2, "answer_items": "Start\nFinish"}):
            with self.assertRaises(ValidationError):
                normalize_question({**data, **change})
        with patch("exams.fixed_questions.SystemRandom") as randomizer:
            randomizer.return_value.shuffle.side_effect = lambda values: values.reverse()
            corrected = normalize_question({**data, "answer_items": "Start\nRun\nFinish",
                                            "options_hint": row["options"]})
        self.assertEqual(set(corrected["options"]), {"Start", "Run", "Finish"})

    def test_xlsx_import_keeps_explicit_structured_type_and_key(self):
        book = Workbook()
        sheet = book.active
        sheet.append(["number", "kind", "points", "question", "answer_items",
                      "option_1", "option_2", "option_3", "option_4"])
        sheet.append([1, "cloze", 2, "[[1]] and [[2]]", "one\ntwo"])
        sheet.append([2, "matching", 2, "Match.", "A => X\nB => Y"])
        sheet.append([3, "multiselect", 2, "Choose two.", "1,3", "A", "B", "C", "D"])
        sheet.append([4, "ordering", 3, "Order.", "First\nSecond\nThird"])
        stream = BytesIO()
        book.save(stream)
        rows = read_fixed_questions(SimpleUploadedFile("questions.xlsx", stream.getvalue()))
        self.assertEqual([row["kind"] for row in rows], ["cloze", "matching", "multiselect", "ordering"])
        self.assertEqual(rows[0]["interaction"]["answers"], [["one"], ["two"]])
        self.assertEqual({rows[1]["options"][index] for index in rows[1]["interaction"]["answers"]}, {"X", "Y"})
        self.assertEqual(rows[2]["interaction"]["answers"], [0, 2])
        self.assertEqual({rows[3]["options"][index] for index in rows[3]["interaction"]["answers"]},
                         {"First", "Second", "Third"})
        with self.assertRaisesMessage(ValidationError, "set a structured type"):
            normalize_question({"slot": 1, "points": 1, "stem": "Fill [[1]].", "answer_items": "CPU"})


class StructuredAdminRoundTripTests(TestCase):
    def test_ordering_draft_allows_correcting_an_item(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "password")
        self.client.force_login(admin)
        exam = Exam.objects.create(title="Steps", subject="Science", grade=9, mode=Exam.Mode.FIXED,
                                   question_count=1, total_points=3, created_by=admin)
        question = Question.objects.create(exam=exam, **normalize_question({
            "slot": 1, "kind": "ordering", "points": 3, "stem": "Order the steps.",
            "answer_items": "Start\nProccess\nFinish",
        }))
        response = self.client.post(f"/admin/exams/exam/{exam.pk}/change/", {
            "title": exam.title, "subject": exam.subject, "grade": "9", "duration_minutes": "60",
            "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(question.pk), "questions-0-slot": "1", "questions-0-kind": "ordering",
            "questions-0-grading_mode": "ai", "questions-0-points": "3",
            "questions-0-stem": question.stem, "questions-0-answer_items": "Start\nProcess\nFinish",
        })
        self.assertEqual(response.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(set(question.options), {"Start", "Process", "Finish"})
        self.assertEqual(question.model_answer, "1. Start\n2. Process\n3. Finish")

    def test_multiselect_and_ordering_editor_keeps_saved_keys(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "password")
        self.client.force_login(admin)
        exam = Exam.objects.create(title="Kinds", subject="Science", grade=9, mode=Exam.Mode.FIXED,
                                   question_count=2, total_points=5, created_by=admin)
        multi = Question.objects.create(exam=exam, **normalize_question({
            "slot": 1, "kind": "multiselect", "points": 2, "stem": "Choose two.",
            "options": ["A", "B", "C", "D"], "answer_items": "1,3",
        }))
        order = Question.objects.create(exam=exam, **normalize_question({
            "slot": 2, "kind": "ordering", "points": 3, "stem": "Order.",
            "answer_items": "First\nSecond\nThird",
        }))
        original_options = list(order.options)
        page = self.client.get(f"/admin/exams/exam/{exam.pk}/change/")
        self.assertContains(page, "1,3")
        data = {
            "title": exam.title, "subject": exam.subject, "grade": "9", "duration_minutes": "60",
            "published": "on", "questions-TOTAL_FORMS": "2", "questions-INITIAL_FORMS": "2",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(multi.pk), "questions-0-slot": "1", "questions-0-kind": "multiselect",
            "questions-0-grading_mode": "ai", "questions-0-points": "2",
            "questions-0-stem": multi.stem, "questions-0-answer_items": "1,3",
            "questions-0-option_1": "A", "questions-0-option_2": "B",
            "questions-0-option_3": "C", "questions-0-option_4": "D",
            "questions-1-id": str(order.pk), "questions-1-slot": "2", "questions-1-kind": "ordering",
            "questions-1-grading_mode": "ai", "questions-1-points": "3",
            "questions-1-stem": order.stem, "questions-1-answer_items": "First\nSecond\nThird",
        }
        self.assertEqual(self.client.post(f"/admin/exams/exam/{exam.pk}/change/", data).status_code, 302)
        exam.refresh_from_db()
        self.assertTrue(exam.published)
        multi.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(multi.interaction["answers"], [0, 2])
        self.assertEqual(order.options, original_options)
        self.assertEqual({order.options[index] for index in order.interaction["answers"]},
                         {"First", "Second", "Third"})

    def test_text_import_can_be_changed_to_cloze_in_draft(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "password")
        self.client.force_login(admin)
        exam = Exam.objects.create(title="Terms", subject="Science", grade=9, mode=Exam.Mode.FIXED,
                                   question_count=1, total_points=2, created_by=admin)
        upload = SimpleUploadedFile("questions.txt", b"Question 1 - Terms [2 marks]\nFill [[1]] and [[2]].")
        question = Question.objects.create(exam=exam, **read_fixed_questions(upload)[0])
        self.assertEqual(question.kind, Question.Kind.OPEN)
        data = {
            "title": exam.title, "subject": exam.subject, "grade": "9", "duration_minutes": "60",
            "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(question.pk), "questions-0-slot": "1", "questions-0-kind": "cloze",
            "questions-0-grading_mode": "ai", "questions-0-points": "2",
            "questions-0-stem": question.stem, "questions-0-answer_items": "CPU\nRAM",
        }
        self.assertEqual(self.client.post(f"/admin/exams/exam/{exam.pk}/change/", data).status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.kind, Question.Kind.CLOZE)
        self.assertEqual(question.interaction["answers"], [["CPU"], ["RAM"]])
        data["questions-0-kind"] = "open"
        data["questions-0-answer_items"] = ""
        response = self.client.post(f"/admin/exams/exam/{exam.pk}/change/", data)
        self.assertContains(response, "Для смены типа составного вопроса загрузите его заново")
        question.refresh_from_db()
        self.assertEqual(question.kind, Question.Kind.CLOZE)

    def test_draft_editor_and_publication_keep_structured_key(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "password")
        self.client.force_login(admin)
        exam = Exam.objects.create(title="Terms", subject="Science", grade=9, mode=Exam.Mode.FIXED,
                                   question_count=2, total_points=4, created_by=admin)
        first = Question.objects.create(exam=exam, **normalize_question({
            "slot": 1, "kind": "cloze", "points": 2, "stem": "[[1]] uses [[2]].",
            "answer_items": "CPU\nRAM",
        }))
        second = Question.objects.create(exam=exam, **normalize_question({
            "slot": 2, "kind": "matching", "points": 2, "stem": "Match.",
            "answer_items": "CPU => processor\nRAM => memory",
        }))
        _, count, points = validate_stored_questions(exam)
        self.assertEqual((count, points), (2, 4))
        page = self.client.get(f"/admin/exams/exam/{exam.pk}/change/")
        self.assertContains(page, "CPU =&gt; processor")
        data = {
            "title": exam.title, "subject": exam.subject, "grade": "9", "duration_minutes": "60",
            "questions-TOTAL_FORMS": "2", "questions-INITIAL_FORMS": "2",
            "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "1000",
            "questions-0-id": str(first.pk), "questions-0-slot": "1", "questions-0-kind": "cloze",
            "questions-0-grading_mode": "ai", "questions-0-points": "2",
            "questions-0-stem": first.stem, "questions-0-answer_items": "CPU\nRAM",
            "questions-1-id": str(second.pk), "questions-1-slot": "2", "questions-1-kind": "matching",
            "questions-1-grading_mode": "ai", "questions-1-points": "2",
            "questions-1-stem": second.stem,
            "questions-1-answer_items": "CPU => processor\nRAM => memory",
        }
        data["published"] = "on"
        self.assertEqual(self.client.post(f"/admin/exams/exam/{exam.pk}/change/", data).status_code, 302)
        exam.refresh_from_db()
        self.assertTrue(exam.published)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.interaction["answers"], [["CPU"], ["RAM"]])
        self.assertEqual({second.options[index] for index in second.interaction["answers"]},
                         {"memory", "processor"})
