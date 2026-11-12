from copy import deepcopy
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .management.commands.repair_fo_sor_rubrics import REVISIONS
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class RubricRepairTests(TestCase):
    def setUp(self):
        teacher = get_user_model().objects.create_superuser("rubric-teacher", "teacher@example.invalid", "password")
        self.user = get_user_model().objects.create_user("rubric-student@example.invalid", password="password")
        student = Student.objects.create(user=self.user, name="Synthetic Student", grade=10,
                                         password_change_required=False)
        self.sources = {}
        self.copies = {}
        self.invitations = {}
        for exam_id in (13, 14):
            exam = Exam.objects.create(
                id=exam_id, title=f"Synthetic {exam_id}", subject="Computer Science", grade=10,
                mode=Exam.Mode.FIXED, published=True, answers_released=True,
                question_count=11 if exam_id == 13 else 20, total_points=100, created_by=teacher)
            invitation = Invitation.objects.create(
                exam=exam, student=student if exam_id == 13 else Student.objects.create(
                    user=get_user_model().objects.create_user(f"other-{exam_id}@example.invalid"),
                    name="Other Synthetic Student", grade=10, password_change_required=False),
                code_hash=f"synthetic-{exam_id}", expires_at=timezone.now() + timedelta(hours=1),
                variant_status=Invitation.VariantStatus.READY, created_by=teacher)
            self.invitations[exam_id] = invitation
            for key, revision in REVISIONS.items():
                if key[0] != exam_id:
                    continue
                kwargs = dict(
                    exam=exam, slot=key[1], kind=Question.Kind.OPEN,
                    grading_mode=Question.GradingMode.AI,
                    points=sum(row["points"] for row in revision["old"]), stem=revision["stem"],
                    rubric=deepcopy(revision["old"]),
                    model_answer=revision.get("old_model_answer", "Example answer"))
                self.sources[key] = Question.objects.create(**kwargs)
                self.copies[key] = Question.objects.create(invitation=invitation, **kwargs)
        grades = {str(q.pk): {"score": 0, "criteria": [0] * len(q.rubric), "explanation": "Old grade"}
                  for key, q in self.copies.items() if key[0] == 13}
        self.attempt = Attempt.objects.create(
            invitation=self.invitations[13], status=Attempt.Status.DONE, score=0,
            question_ids=[q.pk for key, q in self.copies.items() if key[0] == 13], grades=grades)
        self.appeal = GradeAppeal.objects.create(
            attempt=self.attempt, question=self.copies[(13, 1)],
            student_reason="Please review the score.", original_grade=deepcopy(grades[str(self.copies[(13, 1)].pk)]))

    def test_apply_updates_template_and_copies_but_preserves_old_result_until_regrade(self):
        old_q1 = deepcopy(REVISIONS[(13, 1)]["old"])
        call_command("repair_fo_sor_rubrics", stdout=StringIO())
        self.sources[(13, 1)].refresh_from_db()
        self.assertEqual(self.sources[(13, 1)].rubric, old_q1)

        call_command("repair_fo_sor_rubrics", apply=True, stdout=StringIO())
        for key, revision in REVISIONS.items():
            self.sources[key].refresh_from_db()
            self.copies[key].refresh_from_db()
            self.assertEqual(self.sources[key].rubric, revision["new"])
            self.assertEqual(self.copies[key].rubric, revision["new"])
        self.attempt.refresh_from_db()
        self.appeal.refresh_from_db()
        saved_grade = self.attempt.grades[str(self.copies[(13, 1)].pk)]
        self.assertEqual(self.attempt.score, 0)
        self.assertEqual(saved_grade["rubric_snapshot"], old_q1)
        self.assertEqual(saved_grade["model_answer_snapshot"], REVISIONS[(13, 1)]["old_model_answer"])
        self.assertNotIn("rubric_snapshot", self.appeal.original_grade)

        self.client.force_login(self.user)
        result = self.client.get(reverse("exam_result_history", args=[self.attempt.pk]))
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, old_q1[1]["criterion"])
        self.assertNotContains(result, REVISIONS[(13, 1)]["new"][1]["criterion"])
        self.assertContains(result, REVISIONS[(13, 1)]["old_model_answer"])
        self.assertNotContains(result, REVISIONS[(13, 1)]["new_model_answer"])

        call_command("repair_fo_sor_rubrics", apply=True, stdout=StringIO())
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.grades[str(self.copies[(13, 1)].pk)], saved_grade)

    def test_active_attempt_prevents_any_change(self):
        Attempt.objects.create(
            invitation=self.invitations[14], status=Attempt.Status.ACTIVE,
            question_ids=[self.copies[(14, 20)].pk])
        with self.assertRaisesMessage(CommandError, "active or being graded"):
            call_command("repair_fo_sor_rubrics", apply=True, stdout=StringIO())
        self.sources[(13, 1)].refresh_from_db()
        self.assertEqual(self.sources[(13, 1)].rubric, REVISIONS[(13, 1)]["old"])

    def test_unexpected_question_prevents_partial_change(self):
        question = self.copies[(14, 20)]
        question.stem = "Different question"
        question.save(update_fields=["stem"])
        with self.assertRaisesMessage(CommandError, "Unexpected question data"):
            call_command("repair_fo_sor_rubrics", apply=True, stdout=StringIO())
        self.sources[(13, 1)].refresh_from_db()
        self.assertEqual(self.sources[(13, 1)].rubric, REVISIONS[(13, 1)]["old"])
