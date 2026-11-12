import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from exams.models import Attempt, RegradeJob
from exams.regrade import process_regrade_job


class Command(BaseCommand):
    help = "Regrade selected completed attempts without hiding their original results."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one queued job, then exit.")

    def handle(self, *args, **options):
        while True:
            if Attempt.objects.filter(status__in=(Attempt.Status.PENDING, Attempt.Status.GRADING)).exists():
                if options["once"]:
                    return
                time.sleep(2)
                continue
            with transaction.atomic():
                stale = timezone.now() - timedelta(hours=1)
                job = (RegradeJob.objects.select_for_update(skip_locked=True)
                       .filter(status=RegradeJob.Status.PENDING).order_by("created_at").first())
                if not job:
                    job = (RegradeJob.objects.select_for_update(skip_locked=True)
                           .filter(status=RegradeJob.Status.GRADING, started_at__lt=stale)
                           .order_by("started_at").first())
                if job:
                    job.status = RegradeJob.Status.GRADING
                    job.started_at = timezone.now()
                    job.save(update_fields=["status", "started_at"])
            if not job:
                if options["once"]:
                    return
                time.sleep(2)
                continue
            if not process_regrade_job(job):
                self.stderr.write(f"Could not regrade job {job.pk}.")
            if options["once"]:
                return
