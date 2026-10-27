from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from exams.ai import blueprint
from exams.models import Exam, ExamQuestionRule


TITLE = "ICT & Computer Science Teacher — General Skills Assessment"
BRIEF = Path(__file__).resolve().parents[2] / "content" / "ict_cs_teacher_hiring_brief.md"


class Command(BaseCommand):
    help = "Create the draft English hiring exam for an ICT / Computer Science teacher."

    @transaction.atomic
    def handle(self, *args, **options):
        existing = Exam.objects.filter(title=TITLE, grade=Exam.Grade.EMPLOYEE).first()
        if existing:
            self.stdout.write(f"Exam already exists (ID {existing.pk}); no changes made.")
            return

        exam = Exam(
            title=TITLE,
            subject="ICT / Computer Science",
            grade=Exam.Grade.EMPLOYEE,
            syllabus_text=BRIEF.read_text(encoding="utf-8").split("\n## Curriculum references", 1)[0].strip(),
            question_count=20,
            total_points=60,
            duration_minutes=90,
            auto_structure=False,
            published=False,
        )
        exam.full_clean()
        exam.save()
        ExamQuestionRule.objects.bulk_create([
            ExamQuestionRule(exam=exam, kind=ExamQuestionRule.Kind.MCQ, count=10, points_each=1),
            ExamQuestionRule(exam=exam, kind=ExamQuestionRule.Kind.OPEN, count=10, points_each=5),
        ])
        blueprint(exam)
        self.stdout.write(f"Created draft exam ID {exam.pk}: {exam.title} (20 questions, 60 points, 90 minutes).")
