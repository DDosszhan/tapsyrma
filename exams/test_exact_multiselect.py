from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from .admin import FixedQuestionForm
from .fixed_questions import normalize_question, validate_stored_questions
from .grading import grade_attempt, grade_structured
from .models import Attempt, Exam, Invitation, Question, Student
from .views import _read_answers


EXACT_QUESTION = {
    "slot": 1, "kind": "multiselect", "points": 2, "stem": "Select two correct statements.",
    "options": ["Correct A", "Wrong A", "Correct B", "Wrong B"],
    "answer_items": "1,3", "selection_rule": "exact_correct_only",
}


class ExactMultiselectRulesTests(SimpleTestCase):
    def setUp(self):
        self.row = normalize_question(EXACT_QUESTION)
        self.question = Question(id=42, **self.row)

    def test_rule_is_explicit_and_only_applies_to_multiselect(self):
        self.assertEqual(self.row["interaction"], {"answers": [0, 2], "selection_rule": "exact_correct_only"})
        self.assertIn("Select exactly 2 options", self.row["rubric"][0]["criterion"])
        self.assertIn("no penalty", self.row["rubric"][0]["criterion"])
        with self.assertRaisesMessage(ValidationError, "unsupported multiple-selection rule"):
            normalize_question({**EXACT_QUESTION, "selection_rule": "unsupported"})
        with self.assertRaisesMessage(ValidationError, "requires a multiselect question"):
            normalize_question({"slot": 1, "kind": "open", "points": 1,
                                "stem": "Explain.", "selection_rule": "exact_correct_only"})

    def test_autosave_allows_partial_but_final_submission_requires_two(self):
        request = RequestFactory().post("/exam/save/", {"q_42_0": "1"})
        self.assertEqual(_read_answers(request, [self.question]), {"42": ["0"]})
        with self.assertRaisesMessage(ValueError, "Select exactly 2"):
            _read_answers(request, [self.question], final=True)
        empty = RequestFactory().post("/exam/submit/", {})
        self.assertEqual(_read_answers(empty, [self.question], final=True), {"42": []})
        complete = RequestFactory().post("/exam/submit/", {"q_42_0": "1", "q_42_1": "1"})
        self.assertEqual(_read_answers(complete, [self.question], final=True), {"42": ["0", "1"]})

    def test_two_selections_score_each_correct_without_wrong_penalty(self):
        self.assertEqual(grade_structured(self.question, ["0", "2"])["score"], 2)
        self.assertEqual(grade_structured(self.question, ["0", "1"])["score"], 1)
        self.assertEqual(grade_structured(self.question, ["1", "3"])["score"], 0)
        partial = grade_structured(self.question, ["0"])
        self.assertEqual(partial["score"], 0)
        self.assertIn("incomplete", partial["explanation"])
        with self.assertRaises(ValueError):
            grade_structured(self.question, ["0", "1", "2"])

    def test_default_multiselect_keeps_previous_scoring(self):
        default = Question(**normalize_question({key: value for key, value in EXACT_QUESTION.items()
                                                 if key != "selection_rule"}))
        self.assertEqual(grade_structured(default, ["0"])["score"], 1)
        self.assertEqual(grade_structured(default, ["0", "1"])["score"], 0)


class ExactMultiselectRoundTripTests(TestCase):
    def test_draft_editor_and_preview_keep_exact_rule(self):
        admin = get_user_model().objects.create_superuser("admin", "admin@example.invalid", "password")
        exam = Exam.objects.create(title="Exact selection", subject="Science", grade=9,
                                   mode=Exam.Mode.FIXED, question_count=1, total_points=2, created_by=admin)
        question = Question.objects.create(exam=exam, **normalize_question(EXACT_QUESTION))
        self.assertEqual(validate_stored_questions(exam)[1:], (1, 2))

        form = FixedQuestionForm(instance=question, data={
            "slot": "1", "kind": "multiselect", "grading_mode": "ai", "points": "2",
            "stem": "Select two correct statements.", "answer_items": "1,3",
            "option_1": "Correct A", "option_2": "Wrong A",
            "option_3": "Correct B", "option_4": "Wrong B",
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        question.refresh_from_db()
        self.assertEqual(question.interaction["selection_rule"], "exact_correct_only")
        self.assertIn("Select exactly 2 options", question.rubric[0]["criterion"])
        self.assertEqual(validate_stored_questions(exam)[1:], (1, 2))

        self.client.force_login(admin)
        preview = self.client.get(f"/exam/preview/{exam.pk}/")
        self.assertContains(preview, 'data-exact-choices="2"')
        self.assertContains(preview, "Select exactly 2 options.")
        self.assertNotContains(preview, "Select up to 2 options.")

    def test_student_can_save_partial_but_must_choose_two_to_submit(self):
        teacher = get_user_model().objects.create_superuser("teacher", "teacher@example.invalid", "password")
        user = get_user_model().objects.create_user("student@example.invalid", password="password")
        student = Student.objects.create(user=user, name="Student", grade=9, password_change_required=False)
        exam = Exam.objects.create(title="Exact selection", subject="Science", grade=9,
                                   mode=Exam.Mode.FIXED, question_count=1, total_points=2,
                                   published=True, created_by=teacher)
        Question.objects.create(exam=exam, **normalize_question(EXACT_QUESTION))
        invitation = Invitation.objects.create(
            exam=exam, student=student, code_hash="exact-selection-test",
            expires_at=timezone.now() + timedelta(hours=1), created_by=teacher)
        call_command("prepare_invitations", once=True)
        question = invitation.questions.get()
        self.assertEqual(question.interaction["selection_rule"], "exact_correct_only")
        self.client.force_login(user)
        session = self.client.session
        session["invitation_id"] = invitation.pk
        session.save()

        self.assertEqual(self.client.post("/exam/").status_code, 302)
        page = self.client.get("/exam/")
        self.assertContains(page, 'data-exact-choices="2"')
        self.assertContains(page, "Select exactly 2 options.")
        partial = {f"q_{question.pk}_0": "1"}
        self.assertEqual(self.client.post("/exam/save/", partial).status_code, 200)
        attempt = Attempt.objects.get(invitation=invitation)
        self.assertEqual(attempt.answers[str(question.pk)], ["0"])
        self.assertEqual(self.client.post("/exam/submit/", partial).status_code, 400)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, Attempt.Status.ACTIVE)

        mixed = {f"q_{question.pk}_0": "1", f"q_{question.pk}_1": "1"}
        self.assertEqual(self.client.post("/exam/submit/", mixed).status_code, 302)
        attempt.refresh_from_db()
        grade_attempt(attempt)
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 1)
