from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from courses.models import Course, Lesson, LessonProgress, Section

from .admin import new_code
from .models import Attempt, Exam, GradeAppeal, Invitation, Question, SharedCode, Student


class StudentHomeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.teacher = users.create_user("staff@example.invalid", is_staff=True)
        cls.user = users.create_user("learner@example.invalid", password=settings.STUDENT_INITIAL_PASSWORD)
        cls.student = Student.objects.create(user=cls.user, name="Sample Learner", grade=9,
                                             password_change_required=False)
        cls.other = Student.objects.create(user=users.create_user("other@example.invalid"), name="Another Learner",
                                           grade=9, password_change_required=False)

    def invitation(self, title, *, student=None, published=True):
        exam = Exam.objects.create(title=title, subject="Mathematics", grade=9, published=published,
                                   question_count=1, total_points=20, created_by=self.teacher)
        code, digest = new_code()
        invitation = Invitation.objects.create(exam=exam, student=student or self.student, code_hash=digest,
                                                expires_at=timezone.now() + timedelta(days=1), created_by=self.teacher)
        return invitation, code

    def test_guest_staff_and_empty_student_home(self):
        self.assertContains(self.client.get("/"), reverse("login"))
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get("/"), reverse("admin:index"))
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/dashboard/"), "/")
        page = self.client.get("/")
        self.assertContains(page, 'name="code"')
        self.assertContains(page, "No courses are available for your grade yet.")
        self.assertContains(page, "Your submitted work will appear here.")
        self.assertNotContains(page, "Continue exam")

    def test_new_login_resumes_own_saved_answers_after_start_code_expired(self):
        invitation, _ = self.invitation("Timed practice")
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save(update_fields=["expires_at"])
        question = Question.objects.create(exam=invitation.exam, invitation=invitation, slot=1,
                                           kind=Question.Kind.OPEN, points=20, stem="Explain your reasoning.",
                                           model_answer="Private marking key")
        attempt = Attempt.objects.create(invitation=invitation, deadline_at=timezone.now() + timedelta(minutes=5),
                                         question_ids=[question.pk], answers={str(question.pk): "Saved work"})
        self.assertRedirects(self.client.post(reverse("login"), {
            "username": self.user.username, "password": settings.STUDENT_INITIAL_PASSWORD}), "/")
        self.assertNotIn("invitation_id", self.client.session)
        page = self.client.get("/")
        resume = reverse("exam_resume", args=[attempt.pk])
        self.assertContains(page, resume)
        self.assertContains(page, "Continue exam")
        self.assertNotContains(page, "Private marking key")
        self.assertNotContains(page, "Saved work")
        original_deadline = attempt.deadline_at
        response = self.client.post(resume, follow=True)
        self.assertEqual(response.redirect_chain, [(reverse("exam_attempt", args=[attempt.pk]), 302)])
        self.assertContains(response, "Saved work")
        self.assertEqual(self.client.session["invitation_id"], invitation.pk)
        attempt.refresh_from_db()
        self.assertEqual(attempt.deadline_at, original_deadline)

    def test_learner_can_select_between_two_active_attempts(self):
        self.client.force_login(self.user)
        first, _ = self.invitation("First exam")
        second, _ = self.invitation("Second exam")
        live = Attempt.objects.create(invitation=first, deadline_at=timezone.now() + timedelta(hours=1))
        legacy = Attempt.objects.create(invitation=second, deadline_at=None)
        page = self.client.get("/")
        for attempt in (legacy, live):
            with self.subTest(attempt=attempt.pk):
                url = reverse("exam_resume", args=[attempt.pk])
                self.assertContains(page, url)
                self.assertContains(page, attempt.invitation.exam.title)
                self.assertRedirects(self.client.post(url), reverse("exam_attempt", args=[attempt.pk]))
                self.assertEqual(self.client.session["invitation_id"], attempt.invitation_id)
        other_tab = self.client.get(reverse("exam_attempt", args=[legacy.pk]))
        self.assertContains(other_tab, second.exam.title)
        self.assertNotContains(other_tab, first.exam.title)

    def test_ineligible_attempts_cannot_replace_selected_session(self):
        self.client.force_login(self.user)
        current, _ = self.invitation("Current work")
        current_attempt = Attempt.objects.create(invitation=current)
        self.client.post(reverse("exam_resume", args=[current_attempt.pk]))
        foreign, _ = self.invitation("Other work", student=self.other)
        expired, _ = self.invitation("Just expired")
        draft, _ = self.invitation("Draft work", published=False)
        closed, _ = self.invitation("Submitted work")
        attempts = [
            Attempt.objects.create(invitation=foreign),
            Attempt.objects.create(invitation=expired, deadline_at=timezone.now() - timedelta(seconds=1)),
            Attempt.objects.create(invitation=draft),
            Attempt.objects.create(invitation=closed, status=Attempt.Status.PENDING),
        ]
        page = self.client.get("/")
        for attempt in attempts:
            with self.subTest(attempt=attempt.pk):
                resume = reverse("exam_resume", args=[attempt.pk])
                self.assertNotContains(page, resume)
                self.assertEqual(self.client.post(resume).status_code, 404)
                self.assertEqual(self.client.session["invitation_id"], current.pk)
        for pk in (attempts[0].pk, attempts[-1].pk + 1):
            self.assertEqual(self.client.get(reverse("exam_attempt", args=[pk])).status_code, 404)

    def test_exam_code_normalization_and_csrf_on_home_and_resume(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.user)
        invitation, code = self.invitation("Individual entry")
        browser.get("/")
        posted_code = {"code": f"  {code[:3].lower()}-{code[3:].lower()}  "}
        self.assertEqual(browser.post("/", posted_code).status_code, 403)
        csrf = {"HTTP_X_CSRFTOKEN": browser.cookies[settings.CSRF_COOKIE_NAME].value}
        self.assertRedirects(browser.post("/", posted_code, **csrf), reverse("exam_session"))
        self.assertEqual(browser.session["invitation_id"], invitation.pk)
        shared_code, digest = new_code()
        shared = SharedCode.objects.create(code_hash=digest, expires_at=timezone.now() + timedelta(hours=1),
                                           created_by=self.teacher)
        self.assertRedirects(browser.post("/", {"code": shared_code}, **csrf), reverse("choose_exam"))
        self.assertEqual(browser.session["shared_code_id"], shared.pk)
        attempt = Attempt.objects.create(invitation=invitation)
        resume = reverse("exam_resume", args=[attempt.pk])
        self.assertEqual(browser.get(resume).status_code, 405)
        self.assertEqual(browser.post(resume).status_code, 403)
        self.assertRedirects(browser.post(resume, **csrf), reverse("exam_attempt", args=[attempt.pk]))

    def test_course_progress_only_counts_current_students_opened_lessons(self):
        self.client.force_login(self.user)
        course = Course.objects.create(title="Class course", subject="Maths", grade=9,
                                       published=True, created_by=self.teacher)
        other_grade = Course.objects.create(title="Other grade", subject="Maths", grade=10,
                                            published=True, created_by=self.teacher)
        draft = Course.objects.create(title="Draft course", subject="Maths", grade=9, created_by=self.teacher)
        section = Section.objects.create(course=course, title="Geometry")
        first = Lesson.objects.create(section=section, position=1, title="Lines")
        second = Lesson.objects.create(section=section, position=2, title="Angles")
        LessonProgress.objects.create(lesson=first, student=self.student)
        LessonProgress.objects.create(lesson=second, student=self.other)
        page = self.client.get("/")
        self.assertContains(page, course.title)
        self.assertContains(page, "1 of 2 lessons opened")
        self.assertNotContains(page, other_grade.title)
        self.assertNotContains(page, draft.title)

    def test_latest_submitted_work_shows_only_own_status_and_provisional_score(self):
        self.client.force_login(self.user)
        older, _ = self.invitation("Older quiz")
        older_attempt = Attempt.objects.create(invitation=older, status=Attempt.Status.DONE, score=9,
                                               submitted_at=timezone.now() - timedelta(days=1))
        latest, _ = self.invitation("Newer quiz")
        attempt = Attempt.objects.create(invitation=latest, status=Attempt.Status.DONE, score=12,
                                         submitted_at=timezone.now())
        foreign, _ = self.invitation("Another student's quiz", student=self.other)
        foreign_attempt = Attempt.objects.create(invitation=foreign, status=Attempt.Status.DONE, score=17,
                                                 submitted_at=timezone.now())
        question = Question.objects.create(exam=latest.exam, invitation=latest, slot=1,
                                           kind=Question.Kind.OPEN, points=20, model_answer="Private model answer")
        appeal = GradeAppeal.objects.create(attempt=attempt, question=question, original_grade={"score": 12},
                                            student_reason="Private review request")
        page = self.client.get("/")
        self.assertContains(page, reverse("exam_result_history", args=[attempt.pk]))
        self.assertNotContains(page, reverse("exam_result_history", args=[older_attempt.pk]))
        self.assertNotContains(page, reverse("exam_result_history", args=[foreign_attempt.pk]))
        self.assertContains(page, "Provisional score")
        self.assertContains(page, "Appeal pending")
        self.assertRegex(strip_tags(page.content.decode()), r"\b12\s*/\s*20\b")
        self.assertNotContains(page, "Private model answer")
        self.assertNotContains(page, "Private review request")
        appeal.original_grade = {"score": 12, "review_pending": True}
        appeal.save(update_fields=["original_grade"])
        page = self.client.get("/")
        self.assertContains(page, "Awaiting teacher review")
        self.assertNotContains(page, "Appeal pending")
        appeal.student_note = "Please check my answer."
        appeal.save(update_fields=["student_note"])
        self.assertContains(self.client.get("/"), "Appeal pending")
        appeal.status = GradeAppeal.Status.REJECTED
        appeal.save(update_fields=["status"])
        self.assertNotContains(self.client.get("/"), "Provisional score")
        for status in (Attempt.Status.PENDING, Attempt.Status.GRADING, Attempt.Status.ERROR):
            with self.subTest(status=status):
                attempt.status = status
                attempt.save(update_fields=["status"])
                page = self.client.get("/")
                self.assertContains(page, status.label)
                self.assertNotRegex(strip_tags(page.content.decode()), r"\b12\s*/\s*20\b")
