from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .management.commands.enable_fa3_trace_table import NEW_LAYOUT, NEW_STEM, OLD_STEM, TITLE
from .models import Attempt, Exam, Invitation, Question, Student


class Fa3TraceTableSwitchTests(TestCase):
    def test_switches_only_template_and_unstarted_copy(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        exam = Exam.objects.create(
            pk=19, title=TITLE, subject="Computer Science", grade=10, mode=Exam.Mode.FIXED,
            question_count=9, total_points=25, published=True, created_by=teacher)
        rubric = [{"criterion": f"Criterion {i}", "points": 1} for i in range(6)]
        template = Question.objects.create(
            exam=exam, slot=6, kind=Question.Kind.OPEN, points=6, stem=OLD_STEM, rubric=rubric)
        copies = []
        for number in range(2):
            student = Student.objects.create(name=f"Fixture {number}", grade=10)
            invitation = Invitation.objects.create(
                exam=exam, student=student, code_hash=f"trace-layout-{number}",
                expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
                variant_status=Invitation.VariantStatus.READY)
            copies.append(Question.objects.create(
                exam=exam, invitation=invitation, slot=6, kind=Question.Kind.OPEN,
                points=6, stem=OLD_STEM, rubric=rubric))
        old_grade = {"score": 4, "criteria": [1, 1, 1, 1, 0, 0], "explanation": "Original grade"}
        attempt = Attempt.objects.create(
            invitation=copies[0].invitation, question_ids=[copies[0].pk],
            answers={str(copies[0].pk): "Legacy table answer"},
            grades={str(copies[0].pk): old_grade}, score=4, status=Attempt.Status.DONE)

        output = StringIO()
        call_command("enable_fa3_trace_table", stdout=output)
        self.assertIn("2 question copies to switch; 1 attempted copies preserved", output.getvalue())
        template.refresh_from_db()
        self.assertEqual(template.stem, OLD_STEM)

        call_command("enable_fa3_trace_table", "--apply", stdout=StringIO())
        template.refresh_from_db()
        copies[0].refresh_from_db()
        copies[1].refresh_from_db()
        attempt.refresh_from_db()
        for question in (template, copies[1]):
            self.assertEqual((question.stem, question.interaction), (NEW_STEM, NEW_LAYOUT))
        self.assertEqual((copies[0].stem, copies[0].interaction), (OLD_STEM, {}))
        self.assertEqual(attempt.answers[str(copies[0].pk)], "Legacy table answer")
        self.assertEqual(attempt.grades[str(copies[0].pk)], old_grade)
        self.assertEqual(attempt.score, 4)
        output = StringIO()
        call_command("enable_fa3_trace_table", stdout=output)
        self.assertIn("0 question copies to switch", output.getvalue())
