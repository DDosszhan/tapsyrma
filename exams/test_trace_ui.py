"""The trace question accepts answers in its table without changing reference tables."""

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .admin import GradeAppealAdmin


STEM = (
    "Memory contents:\n\n"
    "| Memory address | Contents |\n|---|---|\n| 300 | 4 |\n\n"
    "Complete the trace after JPN LOOP.\n\n"
    "| Iteration | ACC | IX | Contents of address 400 | Does JPN jump? |\n"
    "|---|---|---|---|---|\n"
    "| 1 | | | | |\n| 2, if executed | | | | |\n| 3, if executed | | | | |\n\n"
    "(b) State which value CMP #9 compares with 9 and explain why the program stops."
)
ANSWER = {"version": 1, "rows": [
    {"acc": "6", "ix": "1", "memory": "6", "jump": "Yes"},
    {"acc": "9", "ix": "2", "memory": "9", "jump": "No"},
    {"acc": "", "ix": "", "memory": "", "jump": ""},
], "comparison": "ACC", "reason": "ACC reaches 9, so JPN does not jump and END runs."}


class TraceQuestionUiTests(SimpleTestCase):
    def setUp(self):
        self.exam = SimpleNamespace(title="Trace fixture", total_points=6, duration_minutes=30,
                                    mode="fixed", grade=10)
        self.question = SimpleNamespace(
            id=42, pk=42, kind="open", interaction={"response_layout": "trace_table_v1"},
            stem=STEM, points=6, image=None, options=[])
        self.attempt = SimpleNamespace(
            pk=1, status="done", score=6, deadline_at=None, submitted_at=None,
            invitation=SimpleNamespace(exam=self.exam, student=SimpleNamespace(name="Fixture")))

    def test_preview_has_editable_trace_and_separate_explanation(self):
        html = render_to_string("exams/attempt.html", {
            "attempt": self.attempt, "questions": [self.question],
            "answers": {"42": ANSWER}, "preview": True,
        })
        self.assertIn('data-trace-answer="1"', html)
        self.assertIn('aria-label="Editable trace table"', html)
        self.assertIn('aria-label="Question table"', html)  # Memory reference remains static.
        self.assertEqual(html.count('name="q_42_trace_'), 12)
        self.assertIn('name="q_42_trace_0_acc" value="6"', html)
        self.assertIn('name="q_42_trace_1_jump"', html)
        self.assertIn('<option value="No" selected>No</option>', html)
        self.assertIn('name="q_42_comparison" value="ACC"', html)
        self.assertIn('name="q_42_reason"', html)
        self.assertIn("ACC reaches 9, so JPN does not jump and END runs.", html)
        self.assertNotIn('name="q_42"', html)
        self.assertNotIn("Tables above are read-only", html)
        self.assertIn(".trace-answer-table tbody tr{display:grid", html)
        self.assertIn("question.dataset.traceAnswer?", html)

    def test_result_displays_filled_table_and_legacy_stays_plain_text(self):
        item = {"question": self.question, "answer": "readable text", "trace_answer": ANSWER,
                "answer_rows": [], "grade": {"score": 6, "criteria": [1] * 6, "explanation": ""},
                "appeal": None, "auto_pending": False, "can_add_note": False,
                "map_status": "correct", "map_label": "full marks"}
        html = render_to_string("exams/result.html", {
            "exam": self.exam, "attempt": self.attempt, "items": [item],
            "show_marking_details": False, "visibility_count": 0, "pending_review_count": 0,
        })
        self.assertIn('aria-label="Your completed trace table"', html)
        self.assertIn("<td>6</td>", html)
        self.assertIn("<td>No</td>", html)
        self.assertIn("ACC reaches 9, so JPN does not jump and END runs.", html)
        self.assertNotIn("&#x27;version&#x27;", html)
        self.assertNotIn('aria-label="Editable trace table"', html)

        self.question.interaction = {}
        legacy = render_to_string("exams/attempt.html", {
            "attempt": self.attempt, "questions": [self.question], "answers": {"42": "Old response"},
            "preview": True,
        })
        self.assertIn('name="q_42"', legacy)
        self.assertIn("Old response", legacy)
        self.assertIn("Tables above are read-only", legacy)

    def test_admin_appeal_shows_structured_answer_as_readable_text(self):
        obj = SimpleNamespace(question=self.question, question_id=42,
                              attempt=SimpleNamespace(answers={"42": ANSWER}))
        html = str(GradeAppealAdmin.student_answer(None, obj))
        self.assertIn("Iteration 1: accumulator value = 6", html)
        self.assertIn("Why the program stops: ACC reaches 9", html)
        self.assertNotIn("&#x27;rows&#x27;", html)
