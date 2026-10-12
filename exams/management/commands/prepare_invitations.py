import time
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from exams.ai import generate_round
from exams.models import Exam, Invitation, Question


class Command(BaseCommand):
    help = "Готовит отдельный комплект вопросов для каждого выданного кода."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Подготовить не более одного варианта и выйти.")

    def handle(self, *args, **options):
        while True:
            with transaction.atomic():
                stale = timezone.now() - timedelta(minutes=10)
                invitation = (Invitation.objects.select_for_update(skip_locked=True, of=("self",))
                              .filter(exam__published=True, expires_at__gt=timezone.now(), attempt__isnull=True)
                              .filter(Q(variant_status=Invitation.VariantStatus.PENDING) |
                                      Q(variant_status=Invitation.VariantStatus.GENERATING, generation_started_at__lt=stale))
                              .order_by("created_at").first())
                if invitation:
                    invitation.variant_status = Invitation.VariantStatus.GENERATING
                    invitation.generation_started_at = timezone.now()
                    invitation.generation_error = ""
                    invitation.save(update_fields=["variant_status", "generation_started_at", "generation_error"])
            if not invitation:
                if options["once"]:
                    return
                time.sleep(2)
                continue
            try:
                for _ in range(3):
                    try:
                        questions = generate_round(invitation.exam)
                        with transaction.atomic():
                            # ponytail: one exam lock serializes uniqueness checks; shard if generation throughput grows.
                            Exam.objects.select_for_update().get(pk=invitation.exam_id)
                            existing = {" ".join(s.casefold().split()) for s in
                                        Question.objects.filter(exam_id=invitation.exam_id).values_list("stem", flat=True)}
                            if any(" ".join(q.stem.casefold().split()) in existing for q in questions):
                                raise ValidationError("ИИ повторил вопрос.")
                            locked = Invitation.objects.select_for_update().get(pk=invitation.pk)
                            if locked.variant_status != Invitation.VariantStatus.GENERATING:
                                break
                            for q in questions:
                                q.invitation = locked
                            Question.objects.bulk_create(questions)
                            locked.variant_status = Invitation.VariantStatus.READY
                            locked.generation_error = ""
                            locked.save(update_fields=["variant_status", "generation_error"])
                        break
                    except ValidationError:
                        if _ == 2:
                            raise
            except Exception as exc:
                Invitation.objects.filter(pk=invitation.pk, variant_status=Invitation.VariantStatus.GENERATING).update(
                    variant_status=Invitation.VariantStatus.ERROR, generation_error=str(exc)[:500])
                self.stderr.write(f"Не удалось подготовить вариант для кода {invitation.pk}: {type(exc).__name__}")
            if options["once"]:
                return
