from io import StringIO
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from .ai import blueprint, generate_round
from .management.commands.create_ict_teacher_exam import TITLE
from .models import Exam, ExamQuestionRule


class IctTeacherExamCommandTests(TestCase):
    def test_creates_one_unpublished_employee_exam_with_manual_structure(self):
        call_command("create_ict_teacher_exam", stdout=StringIO())

        exam = Exam.objects.get(title=TITLE)
        self.assertEqual(exam.grade, Exam.Grade.EMPLOYEE)
        self.assertFalse(exam.published)
        self.assertFalse(exam.auto_structure)
        self.assertEqual((exam.question_count, exam.total_points, exam.duration_minutes), (20, 60, 90))
        self.assertIn("Python 3", exam.syllabus_text)
        self.assertIn("Slot 20", exam.syllabus_text)
        self.assertNotIn("Curriculum references", exam.syllabus_text)
        self.assertEqual(
            list(exam.question_rules.order_by("kind").values_list("kind", "count", "points_each")),
            [(ExamQuestionRule.Kind.MCQ, 10, 1), (ExamQuestionRule.Kind.OPEN, 10, 5)],
        )
        self.assertEqual(len(blueprint(exam)), 20)

        with patch("exams.ai.structured_response", side_effect=RuntimeError("capture request")) as response:
            with self.assertRaisesMessage(RuntimeError, "capture request"):
                generate_round(exam)
        instructions, content = response.call_args.args[2:4]
        self.assertIn("Follow the numbered slot plan", instructions)
        self.assertIn("deterministic Python 3", instructions)
        request = json.loads(content)
        self.assertIn("Slot 11", request["syllabus"])
        self.assertEqual(len(request["slots"]), 5)
        self.assertEqual(request["slots"][0], {"slot": 1, "kind": "mcq", "points": 1})

        call_command("create_ict_teacher_exam", stdout=StringIO())
        self.assertEqual(Exam.objects.filter(title=TITLE).count(), 1)

    def test_generation_requests_five_slots_at_a_time(self):
        call_command("create_ict_teacher_exam", stdout=StringIO())
        exam = Exam.objects.get(title=TITLE)
        requests = []
        persisted = []

        def respond(_name, _schema, _instructions, content, **_kwargs):
            payload = json.loads(content)
            requests.append(payload)
            questions = []
            for slot in payload["slots"]:
                is_mcq = slot["kind"] == ExamQuestionRule.Kind.MCQ
                questions.append({
                    "slot": slot["slot"], "kind": slot["kind"], "points": slot["points"],
                    "subtopic": f"Topic {slot['slot']}", "stem": f"Question {slot['slot']}: explain the result.",
                    "options": ["Correct", "Wrong A", "Wrong B", "Wrong C"] if is_mcq else [],
                    "correct_option": 0,
                    "rubric": ([{"criterion": "Correct choice", "points": 1}] if is_mcq else [
                        {"criterion": f"Step {index}", "points": 1} for index in range(1, 6)
                    ]),
                    "model_answer": "Correct" if is_mcq else "A valid explanation.",
                    "subtopic_explanation": "Relevant concepts.",
                    "answer_explanation": "The answer follows from the data.",
                })
            return {"questions": questions}

        with patch("exams.ai.structured_response", side_effect=respond):
            questions = generate_round(exam, on_valid=persisted.append)

        self.assertEqual([len(request["slots"]) for request in requests], [5, 5, 5, 5])
        self.assertEqual([question.slot for question in questions], list(range(1, 21)))
        self.assertEqual([question.slot for question in persisted], list(range(1, 21)))
        self.assertIn(questions[0].stem, requests[1]["existing_questions_to_avoid"])
