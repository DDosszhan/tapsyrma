from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from exams.models import Student

from .jobs import check_answer, explain_answer
from .management.commands.process_lesson_answers import claim_job
from .models import Course, Lesson, LessonAnswer, LessonProgress, LessonQuestion, Section
from .templatetags.course_content import render_lesson_content


class CourseSkeletonTests(TestCase):
    def test_lesson_diagram_markup_is_whitelisted_and_text_is_escaped(self):
        html = str(render_lesson_content(
            "## Place values\n\n[diagram:place-values]\n\n[diagram:cpu-units]\n\n[diagram:as-gates-reference]\n\n<script>alert(1)</script>\n\n[diagram:secret]"
        ))
        self.assertIn("<h3>Place values</h3>", html)
        self.assertIn("place-values.svg", html)
        self.assertIn("hardware/cpu-units.svg", html)
        self.assertIn("as_hardware/as-gates-reference.svg", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("secret.svg", html)
        self.assertIn("&lt;script&gt;", html)

    def setUp(self):
        self.teacher = get_user_model().objects.create_superuser(
            "teacher@example.invalid", "teacher@example.invalid", "test-password")
        self.user = get_user_model().objects.create_user("nine@example.invalid", "nine@example.invalid", "password")
        self.student = Student.objects.create(user=self.user, name="Ученик 9", grade=9, password_change_required=False)
        self.other_user = get_user_model().objects.create_user("other@example.invalid", "other@example.invalid", "password")
        Student.objects.create(user=self.other_user, name="Другой ученик", grade=9, password_change_required=False)
        self.ten_user = get_user_model().objects.create_user("ten@example.invalid", "ten@example.invalid", "password")
        Student.objects.create(user=self.ten_user, name="Ученик 10", grade=10, password_change_required=False)
        self.course = Course.objects.create(title="Информатика", subject="Информатика", grade=9,
            description="Основы программирования", published=True, created_by=self.teacher)
        self.section = Section.objects.create(course=self.course, position=1, title="Алгоритмы")
        self.first = Lesson.objects.create(section=self.section, position=1, title="Переменные", content="Что такое переменная")
        self.second = Lesson.objects.create(section=self.section, position=2, title="Условия")
        self.draft = Course.objects.create(title="Черновик", subject="Математика", grade=9,
            created_by=self.teacher)
        self.ten_course = Course.objects.create(title="Курс 10", subject="Математика", grade=10,
            published=True, created_by=self.teacher)

    def test_courses_are_limited_to_student_grade_and_published_state(self):
        self.assertEqual(self.client.get(reverse("course_list")).status_code, 302)
        self.client.force_login(self.user)
        listing = self.client.get(reverse("course_list"))
        self.assertContains(listing, "Информатика")
        self.assertNotContains(listing, "Черновик")
        self.assertNotContains(listing, "Курс 10")
        self.assertContains(listing, "My courses")
        self.assertEqual(self.client.get(reverse("course_detail", args=[self.draft.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("course_detail", args=[self.ten_course.pk])).status_code, 404)
        self.client.force_login(self.ten_user)
        self.assertContains(self.client.get(reverse("course_list")), "Курс 10")
        self.assertEqual(self.client.get(reverse("section_detail", args=[self.course.pk, self.section.pk])).status_code, 404)

    def test_hardware_topic_is_nested_and_progress_rolls_up(self):
        chapter = Section.objects.create(course=self.course, position=3, title="3. Hardware")
        topic = Section.objects.create(course=self.course, parent=chapter, position=1,
                                       title="3.1 Computer architecture")
        lesson = Lesson.objects.create(section=topic, position=1, title="Inside the CPU")
        self.client.force_login(self.user)

        listing = self.client.get(reverse("course_list"))
        self.assertEqual(listing.context["courses"][0].section_count, 2)
        self.assertEqual(listing.context["courses"][0].lesson_count, 3)
        course_page = self.client.get(reverse("course_detail", args=[self.course.pk]))
        self.assertContains(course_page, "3. Hardware")
        self.assertNotContains(course_page, "3.1 Computer architecture")
        chapter_summary = next(row for row in course_page.context["sections"] if row.pk == chapter.pk)
        self.assertEqual((chapter_summary.lesson_count, chapter_summary.opened_count), (1, 0))

        chapter_page = self.client.get(reverse("section_detail", args=[self.course.pk, chapter.pk]))
        self.assertContains(chapter_page, "3.1 Computer architecture")
        self.assertContains(chapter_page, "0 of 1")
        self.assertNotContains(chapter_page, "Lessons will appear here")
        lesson_page = self.client.get(reverse("lesson_detail", args=[self.course.pk, topic.pk, lesson.pk]))
        self.assertContains(lesson_page, "Inside the CPU")
        self.assertContains(lesson_page, "3. Hardware")
        chapter_summary = next(row for row in self.client.get(reverse("course_detail", args=[self.course.pk])).context["sections"]
                               if row.pk == chapter.pk)
        self.assertEqual((chapter_summary.lesson_count, chapter_summary.opened_count), (1, 1))

    def test_opening_a_lesson_updates_only_own_progress_and_completion_is_separate(self):
        self.client.force_login(self.user)
        section_url = reverse("section_detail", args=[self.course.pk, self.section.pk])
        lesson_url = reverse("lesson_detail", args=[self.course.pk, self.section.pk, self.first.pk])
        self.assertContains(self.client.get(section_url), "0 of 2")
        self.assertEqual(LessonProgress.objects.count(), 0)
        opened = self.client.get(lesson_url)
        self.assertContains(opened, "Что такое переменная")
        self.assertContains(opened, "is-opened")
        self.assertContains(opened, "1 of 2")
        self.assertEqual(LessonProgress.objects.count(), 1)
        self.client.get(lesson_url)
        self.assertEqual(LessonProgress.objects.count(), 1)
        self.assertContains(self.client.get(reverse("course_list")), "1</strong> of 2 lessons opened", html=False)
        self.assertContains(self.client.get(reverse("course_detail", args=[self.course.pk])), "1</strong> of 2 lessons opened", html=False)
        self.assertNotContains(self.client.get(lesson_url), 'class="lesson-tab is-opened is-completed')

        progress = LessonProgress.objects.get(student=self.student, lesson=self.first)
        progress.completed_at = timezone.now()
        progress.save(update_fields=["completed_at"])
        completed = self.client.get(lesson_url)
        self.assertContains(completed, 'class="lesson-tab is-opened is-completed')
        self.assertContains(completed, "Practice completed")
        self.client.force_login(self.other_user)
        self.assertContains(self.client.get(section_url), "0 of 2")
        self.assertEqual(LessonProgress.objects.count(), 1)

    def test_lesson_must_belong_to_the_selected_section(self):
        other_section = Section.objects.create(course=self.course, position=2, title="Данные")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("lesson_detail", args=[
            self.course.pk, other_section.pk, self.first.pk])).status_code, 404)
        self.assertEqual(LessonProgress.objects.count(), 0)

    def test_teacher_can_manage_courses_and_sections(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get("/admin/courses/course/").status_code, 200)
        self.assertEqual(self.client.get(f"/admin/courses/section/{self.section.pk}/change/").status_code, 200)
        self.assertEqual(self.client.get("/courses/").status_code, 403)

        response = self.client.post("/admin/courses/course/add/", {
            "title": "Новый курс", "subject": "Математика", "grade": "9", "description": "Тестовый курс",
            "sections-TOTAL_FORMS": "1", "sections-INITIAL_FORMS": "0",
            "sections-MIN_NUM_FORMS": "0", "sections-MAX_NUM_FORMS": "1000",
            "sections-0-position": "1", "sections-0-title": "Раздел 1",
        })
        self.assertEqual(response.status_code, 302)
        course = Course.objects.get(title="Новый курс")
        self.assertEqual(course.created_by, self.teacher)
        self.assertEqual(course.sections.get().title, "Раздел 1")

    def test_teacher_can_add_lesson_questions_and_invalid_keys_are_rejected(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(f"/admin/courses/lesson/{self.first.pk}/change/"), "Question: fill in the fields")
        response = self.client.post(f"/admin/courses/lesson/{self.first.pk}/change/", {
            "section": self.section.pk, "position": 1, "title": "Переменные", "content": "Что такое переменная",
            "questions-TOTAL_FORMS": 1, "questions-INITIAL_FORMS": 0,
            "questions-MIN_NUM_FORMS": 0, "questions-MAX_NUM_FORMS": 1000,
            "questions-0-position": 1, "questions-0-active": "on", "questions-0-kind": "mcq",
            "questions-0-points": 1, "questions-0-prompt": "2 + 2?",
            "questions-0-option_a": "3", "questions-0-option_b": "4",
            "questions-0-option_c": "5", "questions-0-option_d": "6",
            "questions-0-correct_option": 2,
        })
        self.assertEqual(response.status_code, 302)
        question = LessonQuestion.objects.get(prompt="2 + 2?")
        self.assertEqual(question.correct_option, 2)
        question.correct_option = 7
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_practice_checks_two_types_and_completes_lesson_only_when_all_correct(self):
        mcq = LessonQuestion.objects.create(lesson=self.first, position=1, kind="mcq", prompt="2 + 2?",
            option_a="3", option_b="4", option_c="5", option_d="6", correct_option=2)
        short = LessonQuestion.objects.create(lesson=self.first, position=2, kind="short", prompt="Столица Франции?",
            accepted_answers="Париж\nParis")
        self.client.force_login(self.user)
        url = lambda q: reverse("lesson_answer", args=[self.course.pk, self.section.pk, self.first.pk, q.pk])
        post = lambda q, value: self.client.post(url(q), {"answer": value, "revision": q.revision})
        self.assertEqual(post(mcq, "1").status_code, 302)
        wrong = LessonAnswer.objects.get(question=mcq)
        self.assertEqual(wrong.status, LessonAnswer.Status.INCORRECT)
        self.assertEqual(wrong.explanation_status, LessonAnswer.ExplanationStatus.PENDING)
        self.assertIsNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        explain_url = reverse("lesson_explain", args=[self.course.pk, self.section.pk, self.first.pk, mcq.pk, wrong.pk])
        self.assertContains(self.client.get(reverse("lesson_detail", args=[self.course.pk, self.section.pk, self.first.pk])),
                            "AI is preparing an explanation")
        self.assertEqual(self.client.post(explain_url).status_code, 302)
        wrong.refresh_from_db()
        self.assertEqual(wrong.explanation_status, LessonAnswer.ExplanationStatus.PENDING)
        with patch("courses.jobs.structured_response", return_value={"explanation": "2 + 2 равно 4."}) as ai:
            explain_answer(wrong)
        ai.assert_called_once()
        self.client.post(explain_url)
        self.assertEqual(LessonAnswer.objects.get(pk=wrong.pk).explanation_status,
                         LessonAnswer.ExplanationStatus.READY)
        post(mcq, "2")
        self.assertIsNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        post(short, "  ПАРИЖ  ")
        self.assertIsNotNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(explain_url).status_code, 404)
        self.assertEqual(LessonAnswer.objects.filter(student__user=self.other_user).count(), 0)

    def test_open_answer_is_queued_and_ai_marks_it_without_blocking_request(self):
        question = LessonQuestion.objects.create(lesson=self.first, position=1, kind="open", points=2,
            prompt="Объясните цикл", model_answer="Цикл повторяет действия.",
            grading_criterion="Указывает повторение действий")
        self.client.force_login(self.user)
        url = reverse("lesson_answer", args=[self.course.pk, self.section.pk, self.first.pk, question.pk])
        with patch("courses.jobs.grade_open") as ai:
            self.client.post(url, {"answer": "Цикл повторяет действия.", "revision": question.revision})
            ai.assert_not_called()
        answer = LessonAnswer.objects.get(question=question)
        self.assertEqual(answer.status, LessonAnswer.Status.PENDING)
        with patch("courses.jobs.grade_open", return_value={"score": 2}) as ai:
            check_answer(answer)
        ai.assert_called_once()
        answer.refresh_from_db()
        self.assertEqual(answer.status, LessonAnswer.Status.CORRECT)
        self.assertEqual(answer.criteria_scores, [2])
        self.assertIsNotNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)

    def test_open_answer_shows_fixed_marks_and_specific_ai_feedback(self):
        question = LessonQuestion.objects.create(
            lesson=self.first, position=1, kind="open", points=2,
            prompt="Convert 1010 to decimal.", model_answer="8 + 2 = 10.",
            grading_criterion="1: Correct place values, 8 and 2\n1: Correct decimal answer, 10",
        )
        question.full_clean()
        self.assertEqual(len(question.marking_rows()), 2)
        answer = LessonAnswer.objects.create(
            student=self.student, question=question, question_revision=question.revision,
            answer="8 + 2 = 11",
        )
        with patch("courses.jobs.grade_open", return_value={
            "score": 1, "criteria": [1, 0], "feedback": "8 + 2 equals 10, not 11.",
            "suggestion": "Add the place values again before writing the final answer.",
        }):
            check_answer(answer)
        answer.refresh_from_db()
        self.assertEqual((answer.status, answer.score, answer.criteria_scores),
                         (LessonAnswer.Status.INCORRECT, 1, [1, 0]))
        self.assertEqual(answer.explanation_status, LessonAnswer.ExplanationStatus.READY)
        self.client.force_login(self.user)
        page = self.client.get(reverse("lesson_detail", args=[self.course.pk, self.section.pk, self.first.pk]))
        self.assertContains(page, "1/1")
        self.assertContains(page, "0/1")
        self.assertContains(page, "8 + 2 equals 10, not 11.")
        self.assertContains(page, "Add the place values again")

        question.grading_criterion = "1: Only one mark"
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_student_marking_guidance_appears_only_after_submission_without_the_key(self):
        question = LessonQuestion.objects.create(
            lesson=self.first, position=1, kind="open", points=2,
            prompt="Write 173 as an eight-bit binary number.", model_answer="10101101",
            grading_criterion="1: Decompose 173 as 128 + 32 + 8 + 4 + 1\n1: Final answer 10101101",
            student_guidance="Show a valid conversion method.\nWrite an eight-bit final answer.",
        )
        self.client.force_login(self.user)
        page_url = reverse("lesson_detail", args=[self.course.pk, self.section.pk, self.first.pk])
        before = self.client.get(page_url)
        self.assertNotContains(before, "How marks are awarded")
        self.assertNotContains(before, "10101101")

        answer_url = reverse("lesson_answer", args=[self.course.pk, self.section.pk, self.first.pk, question.pk])
        self.client.post(answer_url, {"answer": "I do not know", "revision": question.revision})
        after = self.client.get(page_url)
        self.assertContains(after, "How marks are awarded")
        self.assertContains(after, "Show a valid conversion method.")
        self.assertContains(after, "Write an eight-bit final answer.")
        self.assertNotContains(after, "10101101")
        self.assertNotContains(after, "128 + 32 + 8 + 4 + 1")

        question.student_guidance = "One hint only."
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_editing_question_invalidates_previous_completion(self):
        question = LessonQuestion.objects.create(lesson=self.first, position=1, kind="short", prompt="2 + 2?",
                                                  accepted_answers="4")
        self.client.force_login(self.user)
        url = reverse("lesson_answer", args=[self.course.pk, self.section.pk, self.first.pk, question.pk])
        self.client.post(url, {"answer": "4", "revision": question.revision})
        self.assertIsNotNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        question.accepted_answers = "four"
        question.save()
        self.assertEqual(question.revision, 2)
        self.assertIsNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        self.assertNotContains(self.client.get(reverse("lesson_detail", args=[self.course.pk, self.section.pk,
                                                                             self.first.pk])), "Correct ·")
        self.client.post(url, {"answer": "four", "revision": 1})
        self.assertEqual(LessonAnswer.objects.filter(question=question).count(), 1)
        self.client.post(url, {"answer": "four", "revision": question.revision})
        self.assertIsNotNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)

    def test_background_queue_claims_open_answer_and_explanation(self):
        question = LessonQuestion.objects.create(lesson=self.first, position=1, kind="open",
            prompt="Что такое цикл?", model_answer="Повторение действий", grading_criterion="Повторение")
        answer = LessonAnswer.objects.create(student=self.student, question=question,
            question_revision=question.revision, answer="Не знаю")
        claimed, kind = claim_job()
        self.assertEqual((claimed.pk, kind), (answer.pk, "grading"))
        self.assertEqual(LessonAnswer.objects.get(pk=answer.pk).status, LessonAnswer.Status.GRADING)
        answer.status = LessonAnswer.Status.INCORRECT
        answer.explanation_status = LessonAnswer.ExplanationStatus.PENDING
        answer.save(update_fields=["status", "explanation_status"])
        claimed, kind = claim_job(prefer_explanation=True)
        self.assertEqual((claimed.pk, kind), (answer.pk, "explanation"))
        self.assertEqual(LessonAnswer.objects.get(pk=answer.pk).explanation_status,
                         LessonAnswer.ExplanationStatus.GENERATING)

    def test_hiding_unsolved_question_recalculates_completion(self):
        first = LessonQuestion.objects.create(lesson=self.first, position=1, kind="short",
                                              prompt="2 + 2?", accepted_answers="4")
        second = LessonQuestion.objects.create(lesson=self.first, position=2, kind="short",
                                               prompt="3 + 3?", accepted_answers="6")
        self.client.force_login(self.user)
        url = reverse("lesson_answer", args=[self.course.pk, self.section.pk, self.first.pk, first.pk])
        self.client.post(url, {"answer": "4", "revision": first.revision})
        self.assertIsNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        second.active = False
        second.save()
        self.assertIsNotNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)

    def test_teacher_can_resolve_answer_needing_review(self):
        question = LessonQuestion.objects.create(lesson=self.first, position=1, kind="open", points=2,
            prompt="Что такое цикл?", model_answer="Повторение действий", grading_criterion="Повторение")
        answer = LessonAnswer.objects.create(student=self.student, question=question,
            question_revision=question.revision, answer="Повтор действий", status=LessonAnswer.Status.REVIEW)
        self.client.force_login(self.teacher)
        admin_url = "/admin/courses/lessonanswer/"
        self.client.post(admin_url, {"action": "mark_correct", "_selected_action": [answer.pk]})
        answer.refresh_from_db()
        self.assertEqual((answer.status, answer.score), (LessonAnswer.Status.CORRECT, 2))
        self.assertIsNotNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
        self.client.post(admin_url, {"action": "mark_incorrect", "_selected_action": [answer.pk]})
        answer.refresh_from_db()
        self.assertEqual((answer.status, answer.score), (LessonAnswer.Status.INCORRECT, 0))
        self.assertIsNone(LessonProgress.objects.get(student=self.student, lesson=self.first).completed_at)
