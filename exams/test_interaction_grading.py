from django.test import SimpleTestCase

from .grading import grade_structured
from .models import Question


class StructuredGradingTests(SimpleTestCase):
    def test_cloze_accepts_declared_alternatives_and_gives_partial_credit(self):
        question = Question(
            kind="cloze", points=2,
            rubric=[{"criterion": "Blank 1", "points": 1}, {"criterion": "Blank 2", "points": 1}],
            interaction={"answers": [["central processing unit", "CPU"], ["RAM"]]},
        )
        self.assertEqual(grade_structured(question, [" cpu ", "ROM"])["criteria"], [1, 0])
        self.assertEqual(grade_structured(question, ["", "ram"])["score"], 1)

    def test_matching_scores_each_pair(self):
        question = Question(
            kind="matching", points=2,
            rubric=[{"criterion": "Pair 1", "points": 1}, {"criterion": "Pair 2", "points": 1}],
            interaction={"left": ["CPU", "RAM"], "answers": [1, 0]},
        )
        self.assertEqual(grade_structured(question, ["1", "1"])["criteria"], [1, 0])
        self.assertEqual(grade_structured(question, "")["criteria"], [0, 0])
        self.assertEqual(grade_structured(question, [])["criteria"], [0, 0])
        with self.assertRaises(ValueError):
            grade_structured(question, ["1"])

    def test_multiselect_penalizes_wrong_choices(self):
        question = Question(
            kind="multiselect", points=2, options=["A", "B", "C", "D"],
            rubric=[{"criterion": "Correct choices", "points": 2}],
            interaction={"answers": [0, 2]},
        )
        self.assertEqual(grade_structured(question, ["0", "2"])["score"], 2)
        self.assertEqual(grade_structured(question, ["0", "1"])["score"], 0)
        self.assertEqual(grade_structured(question, ["0"])["score"], 1)
        self.assertEqual(grade_structured(question, ["0", "1", "2", "3"])["score"], 0)

    def test_ordering_scores_each_position(self):
        question = Question(
            kind="ordering", points=3, options=["C", "A", "B"],
            rubric=[{"criterion": f"Position {index}", "points": 1} for index in range(1, 4)],
            interaction={"answers": [1, 2, 0]},
        )
        self.assertEqual(grade_structured(question, ["1", "0", "2"])["criteria"], [1, 0, 0])
