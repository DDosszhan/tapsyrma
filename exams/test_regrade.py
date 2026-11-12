from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import Attempt, Exam, GradeAppeal, Invitation, Question, RegradeJob, Student


class RegradeTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create(
            username="regrade-admin", is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.exam = Exam.objects.create(
            title="Regrade fixture", subject="Computer Science", grade=9,
            mode=Exam.Mode.FIXED, question_count=1, total_points=1,
            created_by=self.admin, published=True)
        self.old_grade = {"score": 0, "criteria": [0], "explanation": "Old grade"}

    def make_attempt(self, suffix, status=Attempt.Status.DONE, grade=None):
        student = Student.objects.create(name=f"Fixture {suffix}", grade=9)
        invitation = Invitation.objects.create(
            exam=self.exam, student=student, code_hash=f"regrade-{suffix}",
            expires_at=timezone.now() + timedelta(hours=1), created_by=self.admin)
        question = Question.objects.create(
            exam=self.exam, invitation=invitation, slot=1, kind=Question.Kind.OPEN,
            points=1, stem="State a function of the control unit.",
            model_answer="It sends control signals.",
            rubric=[{"criterion": "Sends control signals", "points": 1}])
        saved_grade = grade if grade is not None else self.old_grade
        attempt = Attempt.objects.create(
            invitation=invitation, question_ids=[question.pk],
            answers={str(question.pk): "It coordinates instruction execution."},
            grades={str(question.pk): saved_grade}, score=saved_grade["score"], status=status)
        return attempt, question

    def queue(self, *attempts):
        response = self.client.post("/admin/exams/attempt/", {
            "action": "queue_regrade",
            "_selected_action": [str(attempt.pk) for attempt in attempts],
        })
        self.assertEqual(response.status_code, 302)

    def add_second_open(self, attempt):
        question = Question.objects.create(
            exam=self.exam, invitation=attempt.invitation, slot=2, kind=Question.Kind.OPEN,
            points=1, stem="State one function of a processor clock.",
            model_answer="It synchronizes operations.",
            rubric=[{"criterion": "Synchronizes operations", "points": 1}])
        attempt.question_ids.append(question.pk)
        attempt.answers[str(question.pk)] = "It sets the pace of operations."
        attempt.grades[str(question.pk)] = self.old_grade
        attempt.save(update_fields=["question_ids", "answers", "grades"])
        return question

    def test_action_queues_only_selected_old_unreviewed_attempts_without_changing_results(self):
        done, _ = self.make_attempt("done")
        failed, _ = self.make_attempt("failed", Attempt.Status.ERROR)
        v2, _ = self.make_attempt("v2", grade={**self.old_grade, "grading_version": "semantic-v2"})
        active, _ = self.make_attempt("active", Attempt.Status.ACTIVE)
        teacher_grade = {"score": 1, "criteria": [1], "explanation": "Teacher decision"}
        reviewed, reviewed_question = self.make_attempt("reviewed", grade=teacher_grade)
        unselected, _ = self.make_attempt("unselected")
        GradeAppeal.objects.create(
            attempt=reviewed, question=reviewed_question, student_reason="Review fixture",
            original_grade=self.old_grade, status=GradeAppeal.Status.ACCEPTED,
            corrected_grade=teacher_grade)

        with patch("exams.regrade.grade_open") as ai:
            self.queue(done, failed, v2, active, reviewed)
            self.queue(done, failed)
        ai.assert_not_called()
        self.assertEqual(set(RegradeJob.objects.values_list("attempt_id", flat=True)), {done.pk, failed.pk})
        for attempt in (done, failed, v2, active, reviewed, unselected):
            attempt.refresh_from_db()
            expected = 1 if attempt == reviewed else 0
            self.assertEqual(attempt.grades[str(attempt.question_ids[0])]["score"], expected)
            self.assertEqual(attempt.score, expected)
        job = RegradeJob.objects.get(attempt=done)
        self.assertEqual(job.original_grades, done.grades)
        self.assertEqual(job.original_score, done.score)
        self.assertEqual(job.original_status, Attempt.Status.DONE)

    def test_staff_with_attempt_permission_cannot_queue_or_open_regrade_jobs(self):
        attempt, _ = self.make_attempt("staff")
        staff = get_user_model().objects.create(username="regrade-staff", is_staff=True)
        for model, codenames in (
            (Attempt, ("view_attempt", "change_attempt")),
            (RegradeJob, ("view_regradejob",)),
        ):
            content_type = ContentType.objects.get_for_model(model)
            staff.user_permissions.add(*Permission.objects.filter(
                content_type=content_type, codename__in=codenames))
        self.client.force_login(staff)
        self.client.post("/admin/exams/attempt/", {
            "action": "queue_regrade", "_selected_action": [str(attempt.pk)],
        })
        self.assertFalse(RegradeJob.objects.filter(attempt=attempt).exists())
        self.assertIn(self.client.get("/admin/exams/regradejob/").status_code, (403, 404))

    def test_old_retry_does_not_claim_an_attempt_with_queued_regrade(self):
        queued, _ = self.make_attempt("retry-queued", status=Attempt.Status.ERROR)
        available, _ = self.make_attempt("retry-available", status=Attempt.Status.ERROR)
        self.queue(queued)
        response = self.client.post("/admin/exams/attempt/", {
            "action": "retry_grading",
            "_selected_action": [str(queued.pk), str(available.pk)],
        })
        self.assertEqual(response.status_code, 302)
        queued.refresh_from_db()
        available.refresh_from_db()
        self.assertEqual(queued.status, Attempt.Status.ERROR)
        self.assertEqual(available.status, Attempt.Status.PENDING)
        self.assertEqual(RegradeJob.objects.get(attempt=queued).status, RegradeJob.Status.PENDING)

    def test_regrade_publishes_complete_result_and_keeps_pending_appeal(self):
        attempt, question = self.make_attempt("appeal")
        choice = Question.objects.create(
            exam=self.exam, invitation=attempt.invitation, slot=2, kind=Question.Kind.MCQ,
            points=1, stem="Select the correct option.", options=["A", "B", "C", "D"],
            correct_option=0, rubric=[{"criterion": "Correct option", "points": 1}])
        objective_grade = {"score": 1, "criteria": [1], "explanation": "Correct option."}
        attempt.question_ids.append(choice.pk)
        attempt.answers[str(choice.pk)] = "0"
        attempt.grades[str(choice.pk)] = objective_grade
        attempt.score = 1
        attempt.save(update_fields=["question_ids", "answers", "grades", "score"])
        appeal = GradeAppeal.objects.create(
            attempt=attempt, question=question, student_reason="Please review this answer",
            original_grade=self.old_grade)
        self.queue(attempt)
        job = RegradeJob.objects.get(attempt=attempt)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.score), (Attempt.Status.DONE, 1))
        self.assertEqual(attempt.grades[str(question.pk)], self.old_grade)

        new_grade = {"score": 1, "criteria": [1], "explanation": "Correct alternative",
                     "grading_version": "semantic-v2"}
        with patch("exams.regrade.grade_open", return_value=new_grade) as ai:
            call_command("regrade_attempts", "--once")
        ai.assert_called_once()
        attempt.refresh_from_db()
        job.refresh_from_db()
        appeal.refresh_from_db()
        self.assertEqual((attempt.status, attempt.score), (Attempt.Status.DONE, 2))
        self.assertEqual(attempt.grades[str(question.pk)], new_grade)
        self.assertEqual(attempt.grades[str(choice.pk)], objective_grade)
        self.assertEqual(job.original_grades[str(question.pk)], self.old_grade)
        self.assertEqual(job.candidate_grades[str(question.pk)], new_grade)
        self.assertEqual(job.status, RegradeJob.Status.DONE)
        self.assertEqual(appeal.original_grade, self.old_grade)
        self.assertEqual(appeal.student_reason, "Please review this answer")
        self.assertEqual(appeal.status, GradeAppeal.Status.PENDING)

    def test_failed_regrade_keeps_old_result_visible(self):
        attempt, question = self.make_attempt("api-failure")
        second = self.add_second_open(attempt)
        self.queue(attempt)
        first_new_grade = {"score": 1, "criteria": [1], "explanation": "Correct alternative",
                           "grading_version": "semantic-v2"}
        with patch("exams.regrade.grade_open",
                   side_effect=[first_new_grade, ValidationError("Temporary model failure")]):
            call_command("regrade_attempts", "--once")
        attempt.refresh_from_db()
        job = RegradeJob.objects.get(attempt=attempt)
        self.assertEqual((attempt.status, attempt.score), (Attempt.Status.DONE, 0))
        self.assertEqual(attempt.grades[str(question.pk)], self.old_grade)
        self.assertEqual(attempt.grades[str(second.pk)], self.old_grade)
        self.assertEqual(job.original_grades[str(question.pk)], self.old_grade)
        self.assertEqual(job.status, RegradeJob.Status.ERROR)

    def test_manual_grade_changed_after_queue_is_not_overwritten(self):
        attempt, question = self.make_attempt("manual-race")
        self.queue(attempt)
        teacher_grade = {"score": 1, "criteria": [1], "explanation": "Teacher correction"}
        attempt.grades[str(question.pk)] = teacher_grade
        attempt.score = 1
        attempt.save(update_fields=["grades", "score"])

        with patch("exams.regrade.grade_open", return_value={
            "score": 0, "criteria": [0], "explanation": "Automated grade",
            "grading_version": "semantic-v2",
        }):
            call_command("regrade_attempts", "--once")
        attempt.refresh_from_db()
        self.assertEqual((attempt.score, attempt.grades[str(question.pk)]), (1, teacher_grade))
        self.assertEqual(RegradeJob.objects.get(attempt=attempt).status, RegradeJob.Status.ERROR)

    def test_appeal_resolved_after_queue_is_not_overwritten(self):
        attempt, question = self.make_attempt("appeal-race")
        appeal = GradeAppeal.objects.create(
            attempt=attempt, question=question, student_reason="Review fixture",
            original_grade=self.old_grade)
        self.queue(attempt)
        appeal.status = GradeAppeal.Status.REJECTED
        appeal.teacher_note = "Teacher decision"
        appeal.save(update_fields=["status", "teacher_note"])

        with patch("exams.regrade.grade_open", return_value={
            "score": 1, "criteria": [1], "explanation": "Automated grade",
            "grading_version": "semantic-v2",
        }):
            call_command("regrade_attempts", "--once")
        attempt.refresh_from_db()
        appeal.refresh_from_db()
        self.assertEqual((attempt.score, attempt.grades[str(question.pk)]), (0, self.old_grade))
        self.assertEqual(appeal.status, GradeAppeal.Status.REJECTED)
        self.assertEqual(RegradeJob.objects.get(attempt=attempt).status, RegradeJob.Status.ERROR)

    def test_failed_attempt_finishes_missing_grade_with_new_model(self):
        attempt, first = self.make_attempt("missing", status=Attempt.Status.ERROR)
        second = self.add_second_open(attempt)
        attempt.grades.pop(str(second.pk))
        attempt.save(update_fields=["grades"])
        self.queue(attempt)
        new_grade = {"score": 1, "criteria": [1], "explanation": "Correct alternative",
                     "grading_version": "semantic-v2"}
        with patch("exams.regrade.grade_open", return_value=new_grade) as ai:
            call_command("regrade_attempts", "--once")
        self.assertEqual(ai.call_count, 2)
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.score), (Attempt.Status.DONE, 2))
        self.assertEqual(attempt.grades[str(first.pk)], new_grade)
        self.assertEqual(attempt.grades[str(second.pk)], new_grade)
