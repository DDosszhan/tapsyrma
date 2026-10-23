import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from courses.jobs import check_answer, explain_answer
from courses.models import LessonAnswer


def claim_job(prefer_explanation=False):
    stale = timezone.now() - timedelta(minutes=10)
    order = ("explanation", "grading") if prefer_explanation else ("grading", "explanation")
    with transaction.atomic():
        for kind in order:
            if kind == "grading":
                answer = (LessonAnswer.objects.select_for_update(skip_locked=True)
                          .filter(Q(status=LessonAnswer.Status.PENDING) |
                                  Q(status=LessonAnswer.Status.GRADING, grading_started_at__lt=stale))
                          .order_by("created_at", "id").first())
                if answer:
                    answer.status = LessonAnswer.Status.GRADING
                    answer.grading_started_at = timezone.now()
                    answer.save(update_fields=["status", "grading_started_at"])
                    return answer, kind
            else:
                answer = (LessonAnswer.objects.select_for_update(skip_locked=True)
                          .filter(Q(explanation_status=LessonAnswer.ExplanationStatus.PENDING) |
                                  Q(explanation_status=LessonAnswer.ExplanationStatus.GENERATING,
                                    explanation_started_at__lt=stale))
                          .order_by("created_at", "id").first())
                if answer:
                    answer.explanation_status = LessonAnswer.ExplanationStatus.GENERATING
                    answer.explanation_started_at = timezone.now()
                    answer.save(update_fields=["explanation_status", "explanation_started_at"])
                    return answer, kind
    return None, None


class Command(BaseCommand):
    help = "Grades lesson answers and prepares requested explanations."

    def handle(self, *args, **options):
        prefer_explanation = False
        while True:
            answer, kind = claim_job(prefer_explanation)
            prefer_explanation = not prefer_explanation
            if not answer:
                time.sleep(2)
                continue
            try:
                if kind == "grading":
                    check_answer(answer)
                else:
                    explain_answer(answer)
            except Exception as exc:
                if kind == "grading":
                    LessonAnswer.objects.filter(pk=answer.pk, status=LessonAnswer.Status.GRADING).update(
                        status=LessonAnswer.Status.ERROR, review_reason=type(exc).__name__[:500])
                else:
                    LessonAnswer.objects.filter(pk=answer.pk,
                                                explanation_status=LessonAnswer.ExplanationStatus.GENERATING).update(
                        explanation_status=LessonAnswer.ExplanationStatus.ERROR)
                self.stderr.write(f"Could not process lesson answer {answer.pk}: {type(exc).__name__}")
