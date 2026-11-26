from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .grading import grade_structured
from .management.commands.accept_fa3_q2_descriptive_operands import (
    NEW_KEY, OLD_KEY, OLD_MODEL, RUBRIC, STEM, TITLE,
)
from .management.commands.enable_fa3_q2_grouped_cloze import NEW_ROW, PREVIOUS_GROUPED_ROW
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, RegradeJob, Student


class DescriptiveOperandKeyTests(SimpleTestCase):
    def test_only_explicit_add_and_hash_12_variants_gain_the_first_point(self):
        question = Question(kind=Question.Kind.CLOZE, points=2, interaction=NEW_KEY, rubric=RUBRIC)
        for value in NEW_KEY["answers"][0]:
            with self.subTest(value=value):
                self.assertEqual(grade_structured(question, [value, "Arithmetic"])["criteria"], [1, 1])
        for value in ("ADD 12", "denary value 12", "ADD is opcode, 12 is operand"):
            with self.subTest(value=value):
                self.assertEqual(grade_structured(question, [value, "Arithmetic"])["criteria"], [0, 1])


class DescriptiveOperandRepairTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_superuser(
            "teacher", "teacher@example.invalid", "password")
        self.exam = Exam.objects.create(
            pk=19, title=TITLE, subject="Computer Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=9, total_points=25, published=True, created_by=self.teacher)
        self.template = Question.objects.create(exam=self.exam, **NEW_ROW)

    def make_attempt(self, suffix, answer, *, grouped=False, appeal_status=None):
        student = Student.objects.create(name=f"Fixture {suffix}", grade=10)
        invitation = Invitation.objects.create(
            exam=self.exam, student=student, code_hash=f"descriptive-{suffix}",
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher,
            variant_status=Invitation.VariantStatus.READY)
        question = Question.objects.create(
            exam=self.exam, invitation=invitation,
            **(NEW_ROW if grouped else {
                "slot": 2, "kind": Question.Kind.CLOZE, "points": 2,
                "stem": STEM, "interaction": OLD_KEY, "rubric": RUBRIC,
                "model_answer": OLD_MODEL,
            }))
        grade = grade_structured(question, answer)
        attempt = Attempt.objects.create(
            invitation=invitation, question_ids=[question.pk],
            answers={str(question.pk): answer}, grades={str(question.pk): grade},
            score=grade["score"], status=Attempt.Status.DONE)
        if appeal_status:
            GradeAppeal.objects.create(
                attempt=attempt, question=question, student_reason="Review fixture",
                original_grade=grade, status=appeal_status)
        return attempt, question, grade

    def test_previous_grouped_template_and_attempt_remain_valid(self):
        for field in ("stem", "interaction", "rubric", "model_answer"):
            setattr(self.template, field, PREVIOUS_GROUPED_ROW[field])
        self.template.save(update_fields=["stem", "interaction", "rubric", "model_answer"])
        attempt, question, grade = self.make_attempt(
            "previous-grouped", ["ADD", "#12", "Arithmetic"], grouped=True)
        for field in ("stem", "interaction", "rubric", "model_answer"):
            setattr(question, field, PREVIOUS_GROUPED_ROW[field])
        question.save(update_fields=["stem", "interaction", "rubric", "model_answer"])

        call_command("accept_fa3_q2_descriptive_operands", "--apply", stdout=StringIO())
        self.template.refresh_from_db()
        question.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.template.interaction, PREVIOUS_GROUPED_ROW["interaction"])
        self.assertEqual(question.interaction, PREVIOUS_GROUPED_ROW["interaction"])
        self.assertEqual(attempt.grades[str(question.pk)], grade)

    def test_dry_run_apply_and_repeat_preserve_appeals_and_grouped_copies(self):
        corrected, corrected_question, old_grade = self.make_attempt(
            "corrected", ["ADD is opcode, #12 is operand", "Arithmetic"])
        wrong, wrong_question, wrong_grade = self.make_attempt("wrong", ["ADD 12", "Arithmetic"])
        pending, pending_question, pending_grade = self.make_attempt(
            "pending", ["Opcode ADD Operand #12", "Arithmetic"],
            appeal_status=GradeAppeal.Status.PENDING)
        decided, decided_question, decided_grade = self.make_attempt(
            "decided", ["Opcode: ADD. Operand:#12", "Arithmetic"],
            appeal_status=GradeAppeal.Status.ACCEPTED)
        grouped, grouped_question, grouped_grade = self.make_attempt(
            "grouped", ["ADD", "#12", "Arithmetic"], grouped=True)
        student = Student.objects.create(name="Unattempted fixture", grade=10)
        invitation = Invitation.objects.create(
            exam=self.exam, student=student, code_hash="descriptive-unattempted",
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.teacher,
            variant_status=Invitation.VariantStatus.READY)
        unattempted = Question.objects.create(
            exam=self.exam, invitation=invitation, slot=2, kind=Question.Kind.CLOZE, points=2,
            stem=STEM, interaction=OLD_KEY, rubric=RUBRIC, model_answer=OLD_MODEL)
        saved_answers = {attempt.pk: attempt.answers for attempt in
                         (corrected, wrong, pending, decided, grouped)}

        output = StringIO()
        call_command("accept_fa3_q2_descriptive_operands", stdout=output)
        self.assertIn("3 old question copies, 2 score corrections (+2 points)", output.getvalue())
        self.assertIn("1 pending appeals kept, 1 finalized appeals skipped", output.getvalue())
        corrected.refresh_from_db()
        self.assertEqual(corrected.score, 1)
        corrected_question.refresh_from_db()
        self.assertEqual(corrected_question.interaction, OLD_KEY)

        call_command("accept_fa3_q2_descriptive_operands", "--apply", stdout=StringIO())
        for attempt, question, original in (
                (corrected, corrected_question, old_grade), (wrong, wrong_question, wrong_grade),
                (pending, pending_question, pending_grade), (decided, decided_question, decided_grade),
                (grouped, grouped_question, grouped_grade)):
            attempt.refresh_from_db()
            question.refresh_from_db()
            self.assertEqual(attempt.answers, saved_answers[attempt.pk])
        self.assertEqual(corrected.score, 2)
        self.assertEqual(corrected.grades[str(corrected_question.pk)]["key_correction"]["old_grade"], old_grade)
        self.assertEqual(corrected.grades[str(corrected_question.pk)]["interaction_snapshot"], OLD_KEY)
        self.assertEqual(wrong.score, 1)
        self.assertEqual(wrong.grades[str(wrong_question.pk)]["interaction_snapshot"], OLD_KEY)
        self.assertEqual((pending.score, pending_question.interaction), (2, NEW_KEY))
        pending_appeal = GradeAppeal.objects.get(attempt=pending, question=pending_question)
        self.assertEqual(pending_appeal.status, GradeAppeal.Status.PENDING)
        self.assertEqual(pending_appeal.original_grade, pending_grade)
        self.assertEqual(pending.grades[str(pending_question.pk)]["key_correction"]["old_grade"], pending_grade)
        self.assertEqual((decided.score, decided_question.interaction), (1, OLD_KEY))
        self.assertEqual(grouped_question.interaction, NEW_ROW["interaction"])
        unattempted.refresh_from_db()
        self.assertEqual(unattempted.interaction, OLD_KEY)
        self.template.refresh_from_db()
        self.assertEqual(self.template.interaction, NEW_ROW["interaction"])
        output = StringIO()
        call_command("accept_fa3_q2_descriptive_operands", stdout=output)
        self.assertIn("0 old question copies, 0 score corrections (+0 points)", output.getvalue())

    def test_active_attempt_blocks_everything(self):
        attempt, question, _ = self.make_attempt(
            "active", ["ADD is opcode, #12 is operand", "Arithmetic"])
        attempt.status = Attempt.Status.ACTIVE
        attempt.save(update_fields=["status"])
        with self.assertRaisesMessage(CommandError, "active or awaiting grading"):
            call_command("accept_fa3_q2_descriptive_operands", "--apply", stdout=StringIO())
        question.refresh_from_db()
        self.assertEqual(question.interaction, OLD_KEY)

    def test_pending_regrade_blocks_everything(self):
        attempt, question, _ = self.make_attempt(
            "regrade", ["ADD is opcode, #12 is operand", "Arithmetic"])
        RegradeJob.objects.create(
            attempt=attempt, requested_by=self.teacher,
            original_status=Attempt.Status.DONE,
            original_grades=attempt.grades, original_score=attempt.score)
        with self.assertRaisesMessage(CommandError, "regrade is running"):
            call_command("accept_fa3_q2_descriptive_operands", "--apply", stdout=StringIO())
        question.refresh_from_db()
        self.assertEqual(question.interaction, OLD_KEY)

    def test_rejects_unexpected_grouped_copy(self):
        _, question, _ = self.make_attempt(
            "bad-grouped", ["ADD", "#12", "Arithmetic"], grouped=True)
        question.stem = "Changed stem"
        question.save(update_fields=["stem"])
        with self.assertRaisesMessage(CommandError, "Unexpected grouped FA3 question 2 data"):
            call_command("accept_fa3_q2_descriptive_operands", "--apply", stdout=StringIO())

    def test_repeat_rejects_changed_grade(self):
        attempt, question, _ = self.make_attempt(
            "changed-grade", ["ADD is opcode, #12 is operand", "Arithmetic"])
        call_command("accept_fa3_q2_descriptive_operands", "--apply", stdout=StringIO())
        attempt.refresh_from_db()
        grades = dict(attempt.grades)
        grades[str(question.pk)] = {**grades[str(question.pk)], "criteria": [0, 1], "score": 1}
        attempt.grades = grades
        attempt.score = 1
        attempt.save(update_fields=["grades", "score"])
        with self.assertRaisesMessage(CommandError, "saved grade differs from its key"):
            call_command("accept_fa3_q2_descriptive_operands", stdout=StringIO())
