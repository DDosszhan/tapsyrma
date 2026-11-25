from django.contrib.auth import get_user_model
from django.test import TestCase

from .fixed_questions import normalize_question
from .models import Exam, Question
from .templatetags.exam_tags import lettered_matching


class LetteredMatchingPreviewTests(TestCase):
    def test_lettered_matching_uses_letters_without_numeric_list_markers(self):
        options = ["A. Laser", "B. Magnetic", "C. Flash"]
        self.assertTrue(lettered_matching(options))
        self.assertFalse(lettered_matching(["Laser", "Magnetic", "Flash"]))

        admin = get_user_model().objects.create_superuser(
            "preview-admin", "preview@example.invalid", "test-password"
        )
        exam = Exam.objects.create(
            title="Hardware preview", subject="Computer Science", grade=9,
            mode=Exam.Mode.FIXED, question_count=1, total_points=3,
            duration_minutes=35, created_by=admin,
        )
        Question.objects.create(exam=exam, **normalize_question({
            "slot": 1, "kind": "matching", "points": 3,
            "stem": "Match each device.",
            "answer_items": "1. HDD => B. Magnetic\n2. SSD => C. Flash\n3. Disc => A. Laser",
            "options": options,
        }))
        self.client.force_login(admin)
        response = self.client.get(f"/exam/preview/{exam.pk}/")
        self.assertContains(response, "Choose the lettered option for each item:")
        self.assertContains(response, 'class="matching-choices matching-choices--lettered"')
        self.assertContains(response, '>B</option>')
        self.assertNotContains(response, '>Option 2</option>')
