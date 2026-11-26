from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .grading import grade_attempt
from .management.commands.enable_fa3_q2_grouped_cloze import NEW_ROW
from .management.commands.enable_fa3_trace_table import NEW_LAYOUT, NEW_STEM
from .models import Attempt, Exam, Invitation, Question, Student


class Fa3NewAnswerFlowTests(TestCase):
    def test_save_resume_submit_grade_and_result_with_both_new_layouts(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        user = get_user_model().objects.create_user("student@example.invalid", password="password")
        student = Student.objects.create(user=user, name="Fixture", grade=10, password_change_required=False)
        exam = Exam.objects.create(
            title="Future FA3 fixture", subject="Computer Science", grade=10,
            mode=Exam.Mode.FIXED, question_count=2, total_points=8,
            published=True, created_by=teacher)
        invitation = Invitation.objects.create(
            exam=exam, student=student, code_hash="future-fa3-test",
            expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
            variant_status=Invitation.VariantStatus.READY)
        opcode = Question.objects.create(exam=exam, invitation=invitation, **{**NEW_ROW, "slot": 1})
        trace = Question.objects.create(
            exam=exam, invitation=invitation, slot=2, kind=Question.Kind.OPEN,
            points=6, stem=NEW_STEM, interaction=NEW_LAYOUT,
            rubric=[{"criterion": f"Trace criterion {index}", "points": 1} for index in range(1, 7)],
            model_answer="The second iteration ends with ACC = 9 and JPN does not jump.")
        self.client.force_login(user)
        session = self.client.session
        session["invitation_id"] = invitation.pk
        session.save()
        self.assertEqual(self.client.post("/exam/").status_code, 302)
        page = self.client.get("/exam/")
        self.assertContains(page, f'name="q_{opcode.pk}_2"')
        self.assertContains(page, f'name="q_{trace.pk}_trace_0_acc"')
        self.assertContains(page, f'name="q_{trace.pk}_reason"')
        self.assertNotContains(page, f'name="q_{trace.pk}"')

        partial = {
            f"q_{opcode.pk}_0": "ADD", f"q_{opcode.pk}_1": "#12",
            f"q_{trace.pk}_trace_0_acc": "6", f"q_{trace.pk}_trace_0_jump": "Yes",
            f"q_{trace.pk}_comparison": "ACC",
        }
        self.assertEqual(self.client.post("/exam/save/", partial).status_code, 200)
        attempt = Attempt.objects.get(invitation=invitation)
        self.assertEqual(attempt.answers[str(opcode.pk)], ["ADD", "#12", ""])
        self.assertEqual(attempt.answers[str(trace.pk)]["rows"][0]["acc"], "6")
        resumed = self.client.get("/exam/")
        self.assertContains(resumed, f'name="q_{trace.pk}_trace_0_acc" value="6"')
        self.assertContains(resumed, f'name="q_{trace.pk}_comparison" value="ACC"')

        complete = {
            f"q_{opcode.pk}_0": "ADD", f"q_{opcode.pk}_1": "#12",
            f"q_{opcode.pk}_2": "Arithmetic operation",
            f"q_{trace.pk}_trace_0_acc": "6", f"q_{trace.pk}_trace_0_ix": "1",
            f"q_{trace.pk}_trace_0_memory": "6", f"q_{trace.pk}_trace_0_jump": "Yes",
            f"q_{trace.pk}_trace_1_acc": "9", f"q_{trace.pk}_trace_1_ix": "2",
            f"q_{trace.pk}_trace_1_memory": "9", f"q_{trace.pk}_trace_1_jump": "No",
            f"q_{trace.pk}_comparison": "ACC",
            f"q_{trace.pk}_reason": "ACC becomes 9, so JPN does not jump and END executes.",
        }
        self.assertEqual(self.client.post("/exam/submit/", complete).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.PENDING)
        self.assertEqual(attempt.answers[str(trace.pk)]["rows"][2]["acc"], "")
        ai_grade = {"score": 6, "criteria": [1] * 6, "explanation": "Six criteria met."}
        with patch("exams.grading.grade_open", return_value=ai_grade) as ai:
            grade_attempt(attempt)
        self.assertEqual(ai.call_args.args[1]["comparison"], "ACC")
        self.assertEqual(attempt.score, 8)
        result = self.client.get("/exam/result/")
        self.assertContains(result, 'aria-label="Your completed trace table"')
        self.assertContains(result, "ACC becomes 9, so JPN does not jump and END executes.")
        self.assertNotContains(result, "'rows':")
