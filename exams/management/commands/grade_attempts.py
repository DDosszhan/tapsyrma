import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from exams.grading import grade_attempt
from exams.models import Attempt


def expire_attempts():
    now = timezone.now()
    return Attempt.objects.filter(status=Attempt.Status.ACTIVE, deadline_at__lte=now).update(
        status=Attempt.Status.PENDING, submitted_at=now)


class Command(BaseCommand):
    help = "Grades submitted attempts against fixed criteria."

    def handle(self, *args, **options):
        while True:
            with transaction.atomic():
                expire_attempts()
                stale = timezone.now() - timedelta(hours=1)
                attempt = (Attempt.objects.select_for_update(skip_locked=True)
                           .filter(status=Attempt.Status.PENDING).order_by("submitted_at").first())
                if not attempt:
                    attempt = (Attempt.objects.select_for_update(skip_locked=True)
                               .filter(status=Attempt.Status.GRADING, grading_started_at__lt=stale)
                               .order_by("grading_started_at").first())
                if attempt:
                    attempt.status = Attempt.Status.GRADING
                    attempt.grading_started_at = timezone.now()
                    attempt.save(update_fields=["status", "grading_started_at"])
            if not attempt:
                time.sleep(2)
                continue
            try:
                grade_attempt(attempt)
            except Exception as exc:
                # The answer and rubric remain intact for a teacher to retry or review.
                Attempt.objects.filter(pk=attempt.pk, status=Attempt.Status.GRADING).update(
                    status=Attempt.Status.ERROR, error_message=str(exc)[:500])
                self.stderr.write(f"Could not grade attempt {attempt.pk}: {type(exc).__name__}")
