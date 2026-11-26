import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from .ai import ManualReviewRequired, grade_open
from .models import Question
from .trace_answers import TRACE_LAYOUT, format_trace_answer
from .views import _read_answers


class TraceAnswerTests(SimpleTestCase):
    def setUp(self):
        self.question = Question(
            id=42, kind=Question.Kind.OPEN, points=6, stem="Explain a trace.",
            interaction={"response_layout": TRACE_LAYOUT}, model_answer="The loop stops when ACC reaches 9.",
            rubric=[{"criterion": "Identifies the compared register", "points": 1}],
        )
        self.factory = RequestFactory()

    def test_partial_cells_save_as_structured_answer_and_format_for_ai(self):
        request = self.factory.post("/exam/save/", {
            "q_42_trace_0_acc": "6", "q_42_trace_0_ix": "1",
            "q_42_trace_0_memory": "6", "q_42_trace_0_jump": "Yes",
            "q_42_trace_1_acc": "9", "q_42_trace_1_jump": "No",
            "q_42_comparison": "ACC", "q_42_reason": "ACC reaches 9, so JPN does not jump.",
        })
        answer = _read_answers(request, [self.question])["42"]
        self.assertEqual(answer["version"], 1)
        self.assertEqual(answer["rows"][0], {"acc": "6", "ix": "1", "memory": "6", "jump": "Yes"})
        self.assertEqual(answer["rows"][1]["ix"], "")
        self.assertEqual(answer["rows"][2]["jump"], "")
        self.assertIn("Iteration 3: blank", format_trace_answer(answer))

        model_result = {"criteria": [{"score": 1, "evidence": "ACC"}],
                        "needs_review": False, "review_reason": "", "feedback": "", "suggestion": ""}
        with patch("exams.ai.structured_response", return_value=model_result) as model:
            grade = grade_open(self.question, answer)
        self.assertEqual(grade["score"], 1)
        submitted = json.loads(model.call_args.args[3])["student_answer"]
        self.assertIn("Iteration 1: accumulator value = 6", submitted)
        self.assertIn("Compared value: ACC", submitted)
        self.assertNotIn("'rows':", submitted)

        generated_label = {**model_result, "criteria": [{"score": 1, "evidence": "accumulator value"}]}
        with patch("exams.ai.structured_response", side_effect=[generated_label, model_result]) as model:
            self.assertEqual(grade_open(self.question, answer)["score"], 1)
        self.assertEqual(model.call_count, 2)

        for quote in ("accumulator value = 6", "Iteration 1: accumulator value = 6",
                      "Iteration 1: accumulator value = 6; index value = 1"):
            natural_quote = {**model_result, "criteria": [{"score": 1, "evidence": quote}]}
            with patch("exams.ai.structured_response", side_effect=[natural_quote, natural_quote]) as model:
                self.assertEqual(grade_open(self.question, answer)["score"], 1)
            self.assertEqual(model.call_count, 1)

        row_label = {**model_result, "criteria": [{"score": 1, "evidence": "Iteration 1"}]}
        with patch("exams.ai.structured_response", side_effect=[row_label, model_result]) as model:
            self.assertEqual(grade_open(self.question, answer)["score"], 1)
        self.assertEqual(model.call_count, 2)

    def test_blank_and_invalid_fields_do_not_create_false_answers(self):
        blank = self.factory.post("/exam/save/", {})
        self.assertEqual(_read_answers(blank, [self.question])["42"], "")
        with self.assertRaisesMessage(ValueError, "Invalid jump answer"):
            _read_answers(self.factory.post("/exam/save/", {"q_42_trace_0_jump": "Maybe"}), [self.question])
        with self.assertRaisesMessage(ValueError, "Invalid trace answer field"):
            _read_answers(self.factory.post("/exam/save/", {"q_42_trace_3_acc": "6"}), [self.question])
        with self.assertRaises(ManualReviewRequired):
            grade_open(self.question, {"version": 1, "rows": []})

    def test_legacy_open_answer_stays_a_string(self):
        legacy = Question(id=42, kind=Question.Kind.OPEN, points=6, stem="Explain a trace.")
        request = self.factory.post("/exam/save/", {"q_42": "1: 6, 1, 6, Yes"})
        self.assertEqual(_read_answers(request, [legacy])["42"], "1: 6, 1, 6, Yes")
        self.assertEqual(format_trace_answer("1: 6, 1, 6, Yes"), "1: 6, 1, 6, Yes")
