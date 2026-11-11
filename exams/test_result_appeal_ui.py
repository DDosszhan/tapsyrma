import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class ResultAppealUITests(TestCase):
    def test_appeal_and_review_note_start_closed_with_send_arrow(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid")
        user = get_user_model().objects.create_user("student")
        student = Student.objects.create(user=user, name="Student", grade=9,
                                         password_change_required=False)
        exam = Exam.objects.create(title="Exam", subject="Math", grade=9,
                                   syllabus_text="Topic", question_count=2, total_points=2,
                                   created_by=teacher)
        invitation = Invitation.objects.create(
            exam=exam, student=student, code_hash="appeal-ui-test",
            expires_at=timezone.now() + timedelta(hours=1), created_by=teacher)
        questions = [Question.objects.create(
            exam=exam, invitation=invitation, slot=slot, kind=Question.Kind.OPEN,
            points=1, stem=f"Question {slot}?",
            rubric=[{"criterion": "Answer", "points": 1}]) for slot in (1, 2)]
        grades = {
            str(questions[0].pk): {"score": 1, "criteria": [1], "explanation": "Checked"},
            str(questions[1].pk): {"score": 0, "criteria": [0], "explanation": "Review needed",
                                   "review_pending": True},
        }
        attempt = Attempt.objects.create(
            invitation=invitation, question_ids=[q.pk for q in questions],
            answers={str(q.pk): "My answer" for q in questions}, grades=grades,
            score=1, status=Attempt.Status.DONE)
        GradeAppeal.objects.create(
            attempt=attempt, question=questions[1], student_reason="Automatic grading sent this answer to the teacher.",
            original_grade=grades[str(questions[1].pk)])

        self.client.force_login(user)
        page = self.client.get(f"/exam/result/{attempt.pk}/").content.decode()
        disclosure_tags = re.findall(r'<details class="appeal-compose"([^>]*)>', page)
        self.assertEqual(len(disclosure_tags), 2)
        self.assertTrue(all(not re.search(r"\bopen\b", attrs) for attrs in disclosure_tags))
        for question in questions:
            section = page.split(f'id="question-{question.pk}"', 1)[1].split("</section>", 1)[0]
            self.assertRegex(section, r'<details class="appeal-compose"[^>]*>\s*<summary')
            self.assertIn(f'action="/exam/result/{attempt.pk}/appeal/{question.pk}/"', section)
            self.assertIn('name="reason" minlength="10" maxlength="2000" required', section)
            self.assertIn('type="submit" class="appeal-send"', section)
            self.assertIn('aria-label=', section)
            self.assertIn('↑', section)
