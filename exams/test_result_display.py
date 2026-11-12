"""Student result page keeps questions readable without exposing marking keys."""

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase


class ResultDisplayTests(SimpleTestCase):
    def render_question(self, *, kind, stem, answer="", answer_rows=None, options=None):
        question = SimpleNamespace(
            pk=1, id=1, kind=kind, stem=stem, points=2, image=None,
            options=options or [], rubric=[{"criterion": "SECRET_KEY", "points": 2}],
        )
        item = {"question": question, "answer": answer, "answer_rows": answer_rows or [],
                "grade": {"score": 0, "criteria": [0], "explanation": "No answer was given."},
                "appeal": None, "auto_pending": False, "can_add_note": False,
                "map_status": "wrong", "map_label": "points lost"}
        return render_to_string("exams/result.html", {
            "exam": SimpleNamespace(title="Preview", total_points=2, mode="fixed", grade=10),
            "attempt": SimpleNamespace(pk=1, status="done", score=0),
            "items": [item], "show_marking_details": False,
            "visibility_count": 0, "pending_review_count": 0,
        })

    def test_cloze_shows_readable_blanks_including_nested_brackets(self):
        html = self.render_question(
            kind="cloze", stem="MAR <- [[1]]\nMDR <- M[[[2]]]",
            answer_rows=[("Blank 1", "PC"), ("Blank 2", "MAR")])
        self.assertIn('MAR &lt;- <span aria-label="Blank 1">____</span>', html)
        self.assertIn('MDR &lt;- M[<span aria-label="Blank 2">____</span>]', html)
        self.assertNotIn("[[1]]", html)
        self.assertNotIn("SECRET_KEY", html)

    def test_unanswered_mcq_says_no_answer(self):
        html = self.render_question(
            kind="mcq", stem="Choose a gate.", options=["AND", "OR", "NOT", "XOR"])
        self.assertIn("<p>No answer given</p>", html)
        self.assertNotIn("SECRET_KEY", html)
