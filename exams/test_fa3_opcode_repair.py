from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .grading import grade_structured
from .management.commands.repair_fa3_opcode_operand import NEW_KEY, OLD_KEY, RUBRIC, STEM, TITLE
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class Fa3OpcodeRepairTests(TestCase):
    def test_dry_run_then_repair_only_unambiguously_correct_answers(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        exam = Exam.objects.create(
            pk=19, title=TITLE, subject="Computer Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=1, total_points=2, published=True, created_by=teacher)
        Question.objects.create(exam=exam, slot=2, kind=Question.Kind.CLOZE, points=2,
                                stem=STEM, interaction=OLD_KEY, rubric=RUBRIC)
        cases = [
            (["ADD #12", "Arithmetic Operation"], 2),
            (["ADD - opcode, #12 - operand", "Arithmetic operations"], 2),
            (["OPCODE IS ADD AND OPERAND IS #12", "DATA PROCESSING"], 1),
            (["ADD #12", "Arithmetic operate"], 2),
            (["ADD #12", "Arthimetic"], 2),
            (["ADD#12", "Arithmetic opera"], 2),
            (["ADD 12", "Arithmetic"], 1),
            (["12", "ARTHMETIC"], 1),
            (["ADD #12", "Arithmetic"], 2),
        ]
        attempts = []
        for index, (answer, expected_score) in enumerate(cases):
            student = Student.objects.create(name=f"Fixture {index}", grade=10)
            invitation = Invitation.objects.create(
                exam=exam, student=student, code_hash=f"opcode-repair-{index}",
                expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
                variant_status=Invitation.VariantStatus.READY)
            question = Question.objects.create(
                exam=exam, invitation=invitation, slot=2, kind=Question.Kind.CLOZE,
                points=2, stem=STEM, interaction=OLD_KEY, rubric=RUBRIC)
            old_grade = grade_structured(question, answer)
            attempt = Attempt.objects.create(
                invitation=invitation, question_ids=[question.pk], answers={str(question.pk): answer},
                grades={str(question.pk): old_grade}, score=old_grade["score"], status=Attempt.Status.DONE)
            attempts.append((attempt, question, old_grade, expected_score))

        pending_attempt, pending_question, pending_grade, _ = attempts[1]
        appeal = GradeAppeal.objects.create(
            attempt=pending_attempt, question=pending_question,
            student_reason="Please review", original_grade=pending_grade)
        output = StringIO()
        call_command("repair_fa3_opcode_operand", stdout=output)
        self.assertIn("7 score corrections (+8 points)", output.getvalue())
        self.assertEqual(Question.objects.filter(exam=exam, interaction=NEW_KEY).count(), 0)
        self.assertEqual(Attempt.objects.get(pk=pending_attempt.pk).score, pending_grade["score"])

        call_command("repair_fa3_opcode_operand", "--apply", stdout=StringIO())
        self.assertEqual(Question.objects.filter(exam=exam, interaction=NEW_KEY).count(), len(cases) + 1)
        for attempt, question, old_grade, expected_score in attempts:
            attempt.refresh_from_db()
            grade = attempt.grades[str(question.pk)]
            self.assertEqual(grade["score"], expected_score)
            self.assertEqual(attempt.score, expected_score)
            self.assertEqual(grade["interaction_snapshot"], OLD_KEY)
            if expected_score > old_grade["score"]:
                self.assertEqual(grade["key_correction"]["old_grade"], old_grade)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, GradeAppeal.Status.PENDING)
        self.assertEqual(appeal.original_grade, pending_grade)
        output = StringIO()
        call_command("repair_fa3_opcode_operand", stdout=output)
        self.assertIn("0 score corrections (+0 points)", output.getvalue())
