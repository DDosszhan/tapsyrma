from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .grading import grade_attempt
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, Student


class StructuredQuestionJourneyTests(TestCase):
    def setUp(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        user = get_user_model().objects.create_user("student@example.invalid", password="password")
        student = Student.objects.create(user=user, name="Student", grade=9, password_change_required=False)
        exam = Exam.objects.create(title="Structured test", subject="Science", grade=9,
                                   mode=Exam.Mode.FIXED, question_count=2, total_points=4,
                                   published=True, created_by=teacher)
        invitation = Invitation.objects.create(
            exam=exam, student=student, code_hash="structured-test",
            expires_at=timezone.now() + timedelta(hours=1), created_by=teacher,
            variant_status=Invitation.VariantStatus.READY)
        self.cloze = Question.objects.create(
            exam=exam, invitation=invitation, slot=1, kind="cloze", points=2,
            stem="A CPU uses [[1]] and [[2]].", interaction={"answers": [["register"], ["cache"]]},
            rubric=[{"criterion": "Correct CPU storage term", "points": 1},
                    {"criterion": "Correct fast memory term", "points": 1}],
            model_answer="1. register\n2. cache")
        self.matching = Question.objects.create(
            exam=exam, invitation=invitation, slot=2, kind="matching", points=2,
            stem="Match the devices.", options=["Output", "Input"],
            interaction={"left": ["Keyboard", "Monitor"], "answers": [1, 0]},
            rubric=[{"criterion": "Keyboard → Input", "points": 1},
                    {"criterion": "Monitor → Output", "points": 1}],
            model_answer="Keyboard → Input\nMonitor → Output")
        self.exam = exam
        self.invitation = invitation
        self.client.force_login(user)
        session = self.client.session
        session["invitation_id"] = invitation.pk
        session.save()

    def test_save_resume_grade_result_and_appeal(self):
        self.assertEqual(self.client.post("/exam/").status_code, 302)
        page = self.client.get("/exam/")
        self.assertContains(page, f'name="q_{self.cloze.pk}_0"')
        self.assertContains(page, f'name="q_{self.cloze.pk}_1"')
        self.assertContains(page, f'name="q_{self.matching.pk}_0"')
        self.assertContains(page, "Keyboard")
        self.assertNotContains(page, "Keyboard → Input")
        self.assertNotContains(page, "Correct CPU storage term")

        partial = {f"q_{self.cloze.pk}_0": " register ", f"q_{self.matching.pk}_0": "1"}
        self.assertEqual(self.client.post("/exam/save/", partial).status_code, 200)
        attempt = Attempt.objects.get(invitation=self.invitation)
        self.assertEqual(attempt.answers[str(self.cloze.pk)], ["register", ""])
        self.assertEqual(attempt.answers[str(self.matching.pk)], ["1", ""])
        resumed = self.client.get("/exam/")
        self.assertContains(resumed, 'value="register"')
        self.assertContains(resumed, 'value="1" selected')

        for bad in ({f"q_{self.cloze.pk}_2": "extra"},
                    {f"q_{self.cloze.pk}": "unexpected"},
                    {f"q_{self.matching.pk}_0": "2"},
                    {f"q_{self.matching.pk}_0": "1", f"q_{self.matching.pk}_1": "1"},
                    {f"q_{self.matching.pk}_0": "01"},
                    {f"q_{self.matching.pk}_00": "1"}):
            self.assertEqual(self.client.post("/exam/save/", bad).status_code, 400)
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers[str(self.cloze.pk)], ["register", ""])

        complete = {f"q_{self.cloze.pk}_0": "register", f"q_{self.cloze.pk}_1": "cache",
                    f"q_{self.matching.pk}_0": "1", f"q_{self.matching.pk}_1": "0"}
        self.assertEqual(self.client.post("/exam/submit/", complete).status_code, 302)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.PENDING)
        grade_attempt(attempt)
        self.assertEqual(attempt.score, 4)

        result = self.client.get("/exam/result/")
        self.assertContains(result, "4 / 4.00 points")
        self.assertContains(result, "Blank 1: register")
        self.assertNotContains(result, "Correct CPU storage term")
        self.assertContains(result, "Keyboard: Input")
        self.assertNotContains(result, "Keyboard → Input")
        self.assertNotContains(result, "Correct answers")
        appeal_url = f"/exam/result/{attempt.pk}/appeal/{self.matching.pk}/"
        self.assertEqual(self.client.post(appeal_url, {"reason": "My matching answer is correct."}).status_code, 302)
        self.assertTrue(GradeAppeal.objects.filter(attempt=attempt, question=self.matching).exists())

        self.exam.answers_released = True
        self.exam.save(update_fields=["answers_released"])
        released = self.client.get("/exam/result/")
        self.assertContains(released, "Correct answers")
        self.assertContains(released, "Keyboard → Input")

    def test_multiselect_and_ordering_save_validate_and_show_keys_after_release(self):
        self.exam.question_count = 4
        self.exam.total_points = 9
        self.exam.save(update_fields=["question_count", "total_points"])
        multiple = Question.objects.create(
            exam=self.exam, invitation=self.invitation, slot=3, kind="multiselect", points=2,
            stem="Select the input devices.", options=["Keyboard", "Monitor", "Mouse", "Printer"],
            interaction={"answers": [0, 2]},
            rubric=[{"criterion": "All input devices", "points": 2}],
            model_answer="Keyboard and Mouse")
        ordering = Question.objects.create(
            exam=self.exam, invitation=self.invitation, slot=4, kind="ordering", points=3,
            stem="Put the steps in order.", options=["Third", "First", "Second"],
            interaction={"answers": [1, 2, 0]},
            rubric=[{"criterion": "First step", "points": 1},
                    {"criterion": "Second step", "points": 1},
                    {"criterion": "Third step", "points": 1}],
            model_answer="ORDER SECRET")
        self.assertEqual(self.client.post("/exam/").status_code, 302)
        page = self.client.get("/exam/")
        self.assertContains(page, f'type="checkbox" name="q_{multiple.pk}_0"')
        self.assertContains(page, f'name="q_{ordering.pk}_0"')
        self.assertNotContains(page, "ORDER SECRET")
        self.assertNotContains(page, "All input devices")

        partial = {f"q_{multiple.pk}_0": "1", f"q_{multiple.pk}_2": "1",
                   f"q_{ordering.pk}_0": "1"}
        self.assertEqual(self.client.post("/exam/save/", partial).status_code, 200)
        attempt = Attempt.objects.get(invitation=self.invitation)
        self.assertEqual(attempt.answers[str(multiple.pk)], ["0", "2"])
        self.assertEqual(attempt.answers[str(ordering.pk)], ["1", "", ""])
        resumed = self.client.get("/exam/")
        self.assertContains(resumed, f'name="q_{multiple.pk}_2" value="1" checked')
        self.assertContains(resumed, 'value="1" selected')
        for bad in ({f"q_{multiple.pk}_4": "1"}, {f"q_{multiple.pk}_0": "on"},
                    {f"q_{ordering.pk}_0": "03"}, {f"q_{ordering.pk}_3": "0"},
                    {f"q_{ordering.pk}_0": "1", f"q_{ordering.pk}_1": "1"},
                    {f"q_{ordering.pk}": "unexpected"}):
            self.assertEqual(self.client.post("/exam/save/", bad).status_code, 400)

        complete = {f"q_{multiple.pk}_0": "1", f"q_{multiple.pk}_2": "1",
                    f"q_{ordering.pk}_0": "1", f"q_{ordering.pk}_1": "2",
                    f"q_{ordering.pk}_2": "0"}
        self.assertEqual(self.client.post("/exam/submit/", complete).status_code, 302)
        attempt.refresh_from_db()
        grade_attempt(attempt)
        self.assertEqual(attempt.score, 5)
        result = self.client.get("/exam/result/")
        self.assertContains(result, "Selected options: Keyboard, Mouse")
        self.assertContains(result, "Position 1: First")
        self.assertNotContains(result, "ORDER SECRET")
        self.assertNotContains(result, "All input devices")
        appeal_url = f"/exam/result/{attempt.pk}/appeal/{ordering.pk}/"
        self.assertEqual(self.client.post(appeal_url, {"reason": "My ordering answer is correct."}).status_code, 302)
        self.assertTrue(GradeAppeal.objects.filter(attempt=attempt, question=ordering).exists())

        self.exam.answers_released = True
        self.exam.save(update_fields=["answers_released"])
        released = self.client.get("/exam/result/")
        self.assertContains(released, "Correct options: Keyboard, Mouse")
        self.assertContains(released, "ORDER SECRET")
