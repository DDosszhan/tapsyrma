import time
from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from exams.ai import generate_round
from exams.models import Exam, Invitation, Question


class Command(BaseCommand):
    help = "Prepares a separate question set for each issued code."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Prepare at most one question set and exit.")

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

            claim = [invitation.generation_started_at]

            def persist(question):
                with transaction.atomic():
                    # ponytail: one exam lock serializes whole-variant uniqueness checks.
                    Exam.objects.select_for_update().get(pk=invitation.exam_id)
                    locked = Invitation.objects.select_for_update().get(pk=invitation.pk)
                    if (locked.variant_status != Invitation.VariantStatus.GENERATING
                            or locked.generation_started_at != claim[0]):
                        raise ValidationError("Another process is already preparing this question set.")
                    current = list(Question.objects.filter(invitation=locked).order_by("slot"))
                    if any(row.slot == question.slot for row in current):
                        raise ValidationError("Another process has already prepared this position.")
                    if len(current) + 1 == locked.exam.question_count:
                        signature = tuple(" ".join(q.stem.casefold().split())
                                          for q in sorted([*current, question], key=lambda q: q.slot))
                        others = defaultdict(list)
                        for owner_id, stem in (Question.objects.filter(exam_id=invitation.exam_id,
                                                                       invitation__isnull=False)
                                               .exclude(invitation_id=invitation.pk)
                                               .order_by("invitation_id", "slot")
                                               .values_list("invitation_id", "stem")):
                            others[owner_id].append(" ".join(stem.casefold().split()))
                        if signature in {tuple(stems) for stems in others.values()}:
                            raise ValidationError("AI repeated an entire question set.")
                    question.invitation = locked
                    question.save()
                    claim[0] = timezone.now()
                    locked.generation_started_at = claim[0]
                    locked.save(update_fields=["generation_started_at"])

            try:
                for attempt_number in range(3):
                    try:
                        questions = generate_round(
                            invitation.exam, variation_id=f"{invitation.pk}-{attempt_number + 1}",
                            existing_questions=list(invitation.questions.order_by("slot")), on_valid=persist)
                        for question in questions:
                            if question.pk is None and not Question.objects.filter(
                                    invitation=invitation, slot=question.slot).exists():
                                persist(question)
                        with transaction.atomic():
                            locked = Invitation.objects.select_for_update().get(pk=invitation.pk)
                            if (locked.variant_status != Invitation.VariantStatus.GENERATING
                                    or locked.generation_started_at != claim[0]):
                                break
                            if locked.questions.count() != locked.exam.question_count:
                                raise ValidationError("Not all question positions were prepared.")
                            locked.variant_status = Invitation.VariantStatus.READY
                            locked.generation_error = ""
                            locked.save(update_fields=["variant_status", "generation_error"])
                        break
                    except ValidationError:
                        if attempt_number == 2:
                            raise
            except Exception as exc:
                Invitation.objects.filter(pk=invitation.pk, variant_status=Invitation.VariantStatus.GENERATING).update(
                    variant_status=Invitation.VariantStatus.ERROR, generation_error=str(exc)[:500])
                self.stderr.write(f"Could not prepare the question set for code {invitation.pk}: {type(exc).__name__}")
            if options["once"]:
                return
