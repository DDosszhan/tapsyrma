from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .admin import FixedQuestionForm
from .fixed_questions import answer_items_from_question, normalize_question, validate_stored_questions
from .grading import grade_structured
from .management.commands.accept_fa3_q2_descriptive_operands import NEW_KEY as CORRECTED_OLD_KEY
from .management.commands.enable_fa3_q2_grouped_cloze import (
    NEW_ROW, OLD_KEY, OLD_MODEL, OLD_RUBRIC, OLD_STEM, PREVIOUS_GROUPED_ROW, TITLE,
)
from .management.commands.enable_fa3_trace_table import NEW_STEM as TRACE_STEM
from .models import Attempt, Exam, Invitation, Question, Student


class GroupedClozeTests(SimpleTestCase):
    def test_opcode_and_operand_earn_one_point_together(self):
        question = Question(**NEW_ROW)
        self.assertEqual(question.interaction["group_sizes"], [2, 1])
        self.assertEqual(grade_structured(question, ["ADD", "#12", "Arithmetic"])["criteria"], [1, 1])
        self.assertEqual(grade_structured(question, ["ADD", "# 12", "Arithmetic instruction"])["criteria"], [1, 1])
        self.assertEqual(grade_structured(question, ["ADD", "immediate #12", "Arithmetic instructions"])["criteria"], [1, 1])
        self.assertEqual(grade_structured(question, ["ADD", "12", "Arithmetic"])["criteria"], [0, 1])
        self.assertEqual(grade_structured(question, ["SUB", "# 12", "Arithmetic"])["criteria"], [0, 1])
        self.assertEqual(grade_structured(question, ["ADD", "#12", "Arthmetic"])["criteria"], [1, 1])
        self.assertEqual(grade_structured(question, ["", "", "Arithmetic"])["criteria"], [0, 1])

    def test_group_sizes_and_open_layout_are_explicit(self):
        data = {"slot": 1, "kind": "cloze", "points": 2, "stem": "[[1]] [[2]] [[3]]",
                "answer_items": "ADD\n#12\nArithmetic", "cloze_group_sizes": [2, 1]}
        self.assertEqual(normalize_question(data)["interaction"]["group_sizes"], [2, 1])
        with self.assertRaisesMessage(ValidationError, "cover every blank"):
            normalize_question({**data, "cloze_group_sizes": [1, 1]})
        with self.assertRaisesMessage(ValidationError, "Cloze group size"):
            normalize_question({**data, "cloze_group_sizes": [0, 3]})
        open_data = {"slot": 1, "kind": "open", "points": 1,
                     "stem": TRACE_STEM, "response_layout": "trace_table_v1"}
        open_row = normalize_question(open_data)
        self.assertEqual(open_row["interaction"], {"response_layout": "trace_table_v1"})
        with self.assertRaisesMessage(ValidationError, "unsupported open-answer layout"):
            normalize_question({"slot": 1, "kind": "open", "points": 1,
                                "stem": "Explain.", "response_layout": "unknown"})
        with self.assertRaisesMessage(ValidationError, "exactly two Markdown tables"):
            normalize_question({**open_data, "stem": "Explain."})
        with self.assertRaisesMessage(ValidationError, "trace answer table"):
            normalize_question({**open_data, "stem": TRACE_STEM.replace("Does JPN jump?", "Jump?")})


class Fa3Question2SwitchTests(TestCase):
    def test_draft_editor_preserves_grouping_and_open_table_layout(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        exam = Exam.objects.create(title="Layout fixture", subject="Computer Science", grade=10,
                                   mode=Exam.Mode.FIXED, question_count=2, total_points=3, created_by=teacher)
        grouped = Question.objects.create(exam=exam, **{**NEW_ROW, "slot": 1})
        trace = Question.objects.create(exam=exam, **normalize_question({
            "slot": 2, "kind": "open", "points": 1, "stem": TRACE_STEM,
            "rubric": [{"criterion": "Correct trace", "points": 1}],
            "model_answer": "The trace is correct.", "response_layout": "trace_table_v1",
        }))
        for question in (grouped, trace):
            form = FixedQuestionForm(instance=question, data={
                "slot": str(question.slot), "kind": question.kind, "grading_mode": "ai",
                "points": str(question.points), "stem": question.stem,
                "answer_items": answer_items_from_question(question),
                "criteria_text": "\n".join(f"{row['points']}: {row['criterion']}" for row in question.rubric),
                "model_answer": question.model_answer,
            })
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["_fixed"]["interaction"], question.interaction)
        self.assertEqual(validate_stored_questions(exam)[1:], (2, 3))

    def test_switch_updates_only_unattempted_copies_and_is_idempotent(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        exam = Exam.objects.create(
            pk=19, title=TITLE, subject="Computer Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=9, total_points=25, published=True, created_by=teacher)
        template = Question.objects.create(
            exam=exam, slot=2, kind=Question.Kind.CLOZE, points=2, stem=OLD_STEM,
            interaction=OLD_KEY, rubric=OLD_RUBRIC, model_answer=OLD_MODEL)
        copies = []
        for index in range(2):
            student = Student.objects.create(name=f"Fixture {index}", grade=10)
            invitation = Invitation.objects.create(
                exam=exam, student=student, code_hash=f"q2-switch-{index}",
                expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
                variant_status=Invitation.VariantStatus.READY)
            question = Question.objects.create(
                exam=exam, invitation=invitation, slot=2, kind=Question.Kind.CLOZE, points=2,
                stem=OLD_STEM, interaction=OLD_KEY, rubric=OLD_RUBRIC, model_answer=OLD_MODEL)
            copies.append((invitation, question))
        attempted_invitation, attempted_question = copies[0]
        old_grade = {"score": 1, "criteria": [1, 0], "explanation": "Original grade"}
        attempt = Attempt.objects.create(
            invitation=attempted_invitation, question_ids=[attempted_question.pk],
            answers={str(attempted_question.pk): ["ADD #12", "Arthimetic"]},
            grades={str(attempted_question.pk): old_grade}, score=1, status=Attempt.Status.DONE)

        output = StringIO()
        call_command("enable_fa3_q2_grouped_cloze", stdout=output)
        self.assertIn("2 unattempted question copies", output.getvalue())
        template.refresh_from_db()
        self.assertEqual(template.stem, OLD_STEM)
        call_command("enable_fa3_q2_grouped_cloze", "--apply", stdout=StringIO())
        template.refresh_from_db()
        attempted_question.refresh_from_db()
        copies[1][1].refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(template.interaction, NEW_ROW["interaction"])
        self.assertEqual(copies[1][1].interaction, NEW_ROW["interaction"])
        self.assertEqual(attempted_question.stem, OLD_STEM)
        self.assertEqual(attempt.grades[str(attempted_question.pk)], old_grade)
        self.assertEqual(attempt.score, 1)
        output = StringIO()
        call_command("enable_fa3_q2_grouped_cloze", stdout=output)
        self.assertIn("0 unattempted question copies", output.getvalue())

    def test_switch_migrates_previous_grouped_and_corrected_old_copies(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        exam = Exam.objects.create(
            pk=19, title=TITLE, subject="Computer Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=9, total_points=25, published=True, created_by=teacher)
        template = Question.objects.create(exam=exam, **PREVIOUS_GROUPED_ROW)
        copies = []
        saved_attempts = {}
        for index, row, attempted in (
                (0, PREVIOUS_GROUPED_ROW, True),
                (1, {"slot": 2, "kind": Question.Kind.CLOZE, "points": 2,
                     "stem": OLD_STEM, "interaction": CORRECTED_OLD_KEY,
                     "rubric": OLD_RUBRIC, "model_answer": OLD_MODEL}, True),
                (2, PREVIOUS_GROUPED_ROW, False),
                (3, {"slot": 2, "kind": Question.Kind.CLOZE, "points": 2,
                     "stem": OLD_STEM, "interaction": CORRECTED_OLD_KEY,
                     "rubric": OLD_RUBRIC, "model_answer": OLD_MODEL}, False)):
            student = Student.objects.create(name=f"Fixture {index}", grade=10)
            invitation = Invitation.objects.create(
                exam=exam, student=student, code_hash=f"q2-extended-{index}",
                expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
                variant_status=Invitation.VariantStatus.READY)
            question = Question.objects.create(exam=exam, invitation=invitation, **row)
            if attempted:
                answer = (["ADD", "#12", "Arithmetic"] if index == 0 else ["ADD #12", "Arithmetic"])
                grade = grade_structured(question, answer)
                attempt = Attempt.objects.create(
                    invitation=invitation, question_ids=[question.pk],
                    answers={str(question.pk): answer}, grades={str(question.pk): grade},
                    score=grade["score"], status=Attempt.Status.DONE)
                saved_attempts[attempt.pk] = (attempt.answers, attempt.grades, attempt.score)
            copies.append((question, attempt if attempted else None))

        output = StringIO()
        call_command("enable_fa3_q2_grouped_cloze", stdout=output)
        self.assertIn("3 unattempted question copies", output.getvalue())
        template.refresh_from_db()
        self.assertEqual(template.interaction, PREVIOUS_GROUPED_ROW["interaction"])
        call_command("enable_fa3_q2_grouped_cloze", "--apply", stdout=StringIO())
        template.refresh_from_db()
        self.assertEqual(template.interaction, NEW_ROW["interaction"])
        for index, (question, attempt) in enumerate(copies):
            question.refresh_from_db()
            if attempt:
                attempt.refresh_from_db()
                expected = PREVIOUS_GROUPED_ROW if index == 0 else {"interaction": CORRECTED_OLD_KEY}
                self.assertEqual(question.interaction, expected["interaction"])
                self.assertEqual((attempt.answers, attempt.grades, attempt.score), saved_attempts[attempt.pk])
            else:
                self.assertEqual(question.interaction, NEW_ROW["interaction"])
                self.assertEqual(question.stem, NEW_ROW["stem"])
        output = StringIO()
        call_command("enable_fa3_q2_grouped_cloze", stdout=output)
        self.assertIn("0 unattempted question copies", output.getvalue())
