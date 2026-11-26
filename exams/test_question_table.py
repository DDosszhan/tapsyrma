"""Pipe tables stay aligned without forcing the surrounding prose into monospace."""

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase


STEM = (
    "A school compares two processors.\n"
    "Feature         | Processor A | Processor B\n"
    "Clock speed     | 3.0 GHz     | 3.0 GHz\n"
    "Number of cores | 2           | 4\n\n"
    "Explain which processor has more cores."
)


class QuestionTableTests(SimpleTestCase):
    def setUp(self):
        self.exam = SimpleNamespace(title="Hardware", total_points=1, duration_minutes=35,
                                    mode="fixed", grade=9)
        self.question = SimpleNamespace(pk=1, id=1, kind="open", stem=STEM, points=1,
                                        image=None, options=[])

    def test_attempt_and_result_keep_prose_wrapped_and_table_scrollable(self):
        attempt = SimpleNamespace(
            pk=1, status="done", score=0, deadline_at=None,
            invitation=SimpleNamespace(exam=self.exam, student=SimpleNamespace(name="Student")),
        )
        item = {"question": self.question, "answer": "", "answer_rows": [],
                "grade": {"score": 0, "criteria": [0], "explanation": "No answer was given."},
                "appeal": None, "auto_pending": False, "can_add_note": False,
                "map_status": "wrong", "map_label": "points lost"}
        pages = (
            render_to_string("exams/attempt.html", {
                "attempt": attempt, "questions": [self.question], "answers": {}, "preview": True,
            }),
            render_to_string("exams/result.html", {
                "exam": self.exam, "attempt": attempt, "items": [item],
                "show_marking_details": False, "visibility_count": 0, "pending_review_count": 0,
            }),
        )
        for html in pages:
            with self.subTest(page="result" if "Result map" in html else "attempt"):
                self.assertIn('<p class="question-stem">A school compares two processors.</p>', html)
                self.assertIn('<pre class="question-table-scroll"', html)
                self.assertIn('Feature         | Processor A | Processor B', html)
                self.assertIn('Number of cores | 2           | 4', html)
                self.assertIn('<p class="question-stem">Explain which processor has more cores.</p>', html)
                self.assertIn('Scroll sideways to read the table.', html)
        hint = 'Tables above are read-only. Write any table entries in the Your answer box below.'
        self.assertIn(hint, pages[0])
        self.assertNotIn(hint, pages[1])
