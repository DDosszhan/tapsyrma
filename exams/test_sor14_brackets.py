from copy import deepcopy
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .grading import grade_structured
from .management.commands.accept_sor14_bracketed_registers import (
    NEW_KEY, NEW_RUBRIC, OLD_KEY, OLD_RUBRIC, STEM,
)
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, RegradeJob, Student


class Sor14BracketedRegistersTests(TestCase):
    def setUp(self):
        self.teacher = get_user_model().objects.create_superuser(
            "sor14-teacher", "sor14-teacher@example.invalid", "password")
        self.exam = Exam.objects.create(
            id=14, title="Synthetic SOR", subject="Computer Science", grade=10,
            mode=Exam.Mode.FIXED, published=True, answers_released=True,
            question_count=20, total_points=50, created_by=self.teacher)
        self.source = Question.objects.create(
            exam=self.exam, slot=14, kind=Question.Kind.CLOZE,
            points=3, stem=STEM, interaction=deepcopy(OLD_KEY), rubric=deepcopy(OLD_RUBRIC))

    def make_attempt(self, index, answer, *, extra_points=0):
        user = get_user_model().objects.create_user(f"sor14-{index}@example.invalid", password="password")
        student = Student.objects.create(user=user, name=f"Synthetic Student {index}", grade=10,
                                         password_change_required=False)
        invitation = Invitation.objects.create(
            exam=self.exam, student=student, code_hash=f"sor14-code-{index}",
            expires_at=timezone.now() + timedelta(days=1), created_by=self.teacher,
            variant_status=Invitation.VariantStatus.READY)
        question = Question.objects.create(
            exam=self.exam, invitation=invitation, slot=14, kind=Question.Kind.CLOZE,
            points=3, stem=STEM, interaction=deepcopy(OLD_KEY), rubric=deepcopy(OLD_RUBRIC))
        grade = grade_structured(question, answer)
        question_ids = [question.pk]
        answers = {str(question.pk): answer}
        grades = {str(question.pk): grade}
        if extra_points:
            other = Question.objects.create(
                exam=self.exam, invitation=invitation, slot=15, kind=Question.Kind.MCQ,
                points=extra_points, stem="Separate question", options=["Yes", "No"],
                rubric=[{"criterion": "Correct option", "points": extra_points}], correct_option=0)
            question_ids.append(other.pk)
            answers[str(other.pk)] = "0"
            grades[str(other.pk)] = {"score": extra_points, "criteria": [extra_points],
                                     "explanation": "Correct option."}
        attempt = Attempt.objects.create(
            invitation=invitation, status=Attempt.Status.DONE, score=grade["score"] + extra_points,
            question_ids=question_ids, answers=answers, grades=grades)
        return attempt, question, user

    def test_dry_run_then_apply_corrects_existing_scores_and_keeps_appeals(self):
        complete, complete_q, _ = self.make_attempt(1, ["[PC]", "[MAR]", "[MDR]"], extra_points=5)
        partial, partial_q, _ = self.make_attempt(2, ["[PC]", "MAR", "[MDR]"])
        plain, plain_q, plain_user = self.make_attempt(3, ["PC", "MAR", "MDR"])
        pending = GradeAppeal.objects.create(
            attempt=partial, question=partial_q, student_reason="Please check brackets.",
            original_grade=deepcopy(partial.grades[str(partial_q.pk)]))
        old_complete_grade = deepcopy(complete.grades[str(complete_q.pk)])
        old_other_grade = deepcopy(complete.grades[str(complete.question_ids[1])])
        old_pending_grade = deepcopy(pending.original_grade)

        output = StringIO()
        call_command("accept_sor14_bracketed_registers", stdout=output)
        self.assertIn("Dry run", output.getvalue())
        self.source.refresh_from_db()
        complete.refresh_from_db()
        self.assertEqual(self.source.interaction, OLD_KEY)
        self.assertEqual(complete.score, 5)

        output = StringIO()
        call_command("accept_sor14_bracketed_registers", apply=True, stdout=output)
        self.assertIn("2 score corrections (+5 points)", output.getvalue())
        self.source.refresh_from_db()
        complete_q.refresh_from_db()
        partial_q.refresh_from_db()
        plain_q.refresh_from_db()
        complete.refresh_from_db()
        partial.refresh_from_db()
        plain.refresh_from_db()
        pending.refresh_from_db()
        for question in (self.source, complete_q, partial_q, plain_q):
            self.assertEqual(question.interaction, NEW_KEY)
            self.assertEqual(question.rubric, NEW_RUBRIC)
            self.assertEqual(question.stem, STEM)
            self.assertEqual(question.points, 3)
        self.assertEqual(grade_structured(complete_q, ["[PC]", "[MAR]", "[MDR]"])["score"], 3)
        self.assertEqual(complete.score, 8)
        self.assertEqual(complete.grades[str(complete_q.pk)]["score"], 3)
        self.assertEqual(complete.grades[str(complete_q.pk)]["key_correction"]["old_grade"], old_complete_grade)
        self.assertEqual(complete.grades[str(complete.question_ids[1])], old_other_grade)
        self.assertEqual(partial.score, 3)
        self.assertEqual(plain.score, 3)
        self.assertEqual(pending.status, GradeAppeal.Status.PENDING)
        self.assertEqual(pending.original_grade, old_pending_grade)
        self.assertEqual(plain.grades[str(plain_q.pk)]["rubric_snapshot"], OLD_RUBRIC)
        self.assertEqual(plain.grades[str(plain_q.pk)]["interaction_snapshot"], OLD_KEY)

        self.client.force_login(plain_user)
        result = self.client.get(reverse("exam_result_history", args=[plain.pk]))
        self.assertEqual(result.status_code, 200)
        item = result.context["items"][0]
        self.assertEqual(item["correct_rows"][0], ("Blank 1", "PC"))
        self.assertEqual(item["display_rubric"], OLD_RUBRIC)

        saved_grade = deepcopy(complete.grades[str(complete_q.pk)])
        output = StringIO()
        call_command("accept_sor14_bracketed_registers", apply=True, stdout=output)
        self.assertIn("0 question copies, 0 score corrections", output.getvalue())
        complete.refresh_from_db()
        self.assertEqual(complete.grades[str(complete_q.pk)], saved_grade)
        self.assertEqual(complete.score, 8)

    def test_teacher_decision_is_preserved(self):
        attempt, question, _ = self.make_attempt(4, ["[PC]", "[MAR]", "[MDR]"])
        original = deepcopy(attempt.grades[str(question.pk)])
        teacher_grade = {"score": 2, "criteria": [1, 1, 0], "explanation": "Teacher decision."}
        attempt.grades = {str(question.pk): teacher_grade}
        attempt.score = 2
        attempt.save(update_fields=["grades", "score"])
        appeal = GradeAppeal.objects.create(
            attempt=attempt, question=question, student_reason="Please review.",
            original_grade=original, status=GradeAppeal.Status.ACCEPTED,
            corrected_grade=deepcopy(teacher_grade), reviewed_by=self.teacher)

        call_command("accept_sor14_bracketed_registers", apply=True, stdout=StringIO())
        attempt.refresh_from_db()
        appeal.refresh_from_db()
        self.assertEqual(attempt.score, 2)
        self.assertEqual(attempt.grades[str(question.pk)]["score"], 2)
        self.assertEqual(attempt.grades[str(question.pk)]["explanation"], "Teacher decision.")
        self.assertNotIn("key_correction", attempt.grades[str(question.pk)])
        self.assertEqual(appeal.corrected_grade, teacher_grade)
        self.assertEqual(appeal.original_grade, original)

    def test_active_regrade_blocks_all_changes(self):
        attempt, question, _ = self.make_attempt(5, ["[PC]", "[MAR]", "[MDR]"])
        RegradeJob.objects.create(
            attempt=attempt, requested_by=self.teacher, status=RegradeJob.Status.GRADING,
            original_status=Attempt.Status.DONE, original_grades=deepcopy(attempt.grades),
            original_score=attempt.score)
        with self.assertRaisesMessage(CommandError, "A regrade is running"):
            call_command("accept_sor14_bracketed_registers", apply=True, stdout=StringIO())
        self.source.refresh_from_db()
        question.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.source.interaction, OLD_KEY)
        self.assertEqual(question.interaction, OLD_KEY)
        self.assertEqual(attempt.score, 0)

    def test_active_attempt_uses_new_key_when_submitted(self):
        attempt, question, _ = self.make_attempt(6, ["[PC]", "[MAR]", "[MDR]"])
        attempt.status = Attempt.Status.ACTIVE
        attempt.grades = {}
        attempt.score = 0
        attempt.save(update_fields=["status", "grades", "score"])
        call_command("accept_sor14_bracketed_registers", apply=True, stdout=StringIO())
        self.source.refresh_from_db()
        question.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(self.source.interaction, NEW_KEY)
        self.assertEqual(question.interaction, NEW_KEY)
        self.assertEqual(attempt.status, Attempt.Status.ACTIVE)
        self.assertEqual(attempt.grades, {})
        self.assertEqual(grade_structured(question, attempt.answers[str(question.pk)])["score"], 3)

    def test_pending_attempt_blocks_all_changes(self):
        attempt, question, _ = self.make_attempt(7, ["[PC]", "[MAR]", "[MDR]"])
        attempt.status = Attempt.Status.PENDING
        attempt.save(update_fields=["status"])
        with self.assertRaisesMessage(CommandError, "exam attempt is being graded"):
            call_command("accept_sor14_bracketed_registers", apply=True, stdout=StringIO())
        self.source.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual(self.source.interaction, OLD_KEY)
        self.assertEqual(question.interaction, OLD_KEY)
