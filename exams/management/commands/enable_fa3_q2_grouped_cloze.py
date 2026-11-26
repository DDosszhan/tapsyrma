"""Give future FA3 students separate opcode, operand, and group fields."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.fixed_questions import normalize_question
from exams.management.commands.repair_fa3_opcode_operand import (
    EXAM_ID, NEW_KEY as OLD_KEY, RUBRIC as OLD_RUBRIC, SLOT, STEM as OLD_STEM, TITLE,
)
from exams.models import Attempt, Exam, Invitation, Question, RegradeJob


OLD_MODEL = "1. ADD #12\n2. Arithmetic"
NEW_STEM = ("Consider the instruction:\r\n\r\n```text\r\nADD #12\r\n```\r\n\r\n"
            "Identify each part. The opcode and operand must both be correct for their point.\r\n\r\n"
            "- Opcode: [[1]]\r\n- Operand (include the #): [[2]]\r\n- Instruction group: [[3]]")
NEW_RUBRIC = [
    {"criterion": "Opcode and operand both correct", "points": 1},
    {"criterion": "Instruction group correct", "points": 1},
]
PREVIOUS_GROUPED_ROW = normalize_question({
    "slot": SLOT, "kind": "cloze", "points": 2, "stem": NEW_STEM,
    "answer_items": "ADD\n#12\n" + "|".join(OLD_KEY["answers"][1]),
    "cloze_group_sizes": [2, 1], "rubric": NEW_RUBRIC,
})
NEW_ROW = normalize_question({
    "slot": SLOT, "kind": "cloze", "points": 2, "stem": NEW_STEM,
    "answer_items": "ADD\n#12|# 12|immediate #12\n" + "|".join(
        OLD_KEY["answers"][1] + ["Arithmetic instruction", "Arithmetic instructions"]),
    "cloze_group_sizes": [2, 1], "rubric": NEW_RUBRIC,
})


class Command(BaseCommand):
    help = "Preview or enable separate FA3 question 2 fields for future attempts only."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Update only unattempted question copies.")

    def handle(self, *args, **options):
        from exams.management.commands.accept_fa3_q2_descriptive_operands import NEW_KEY as CORRECTED_OLD_KEY

        with transaction.atomic():
            exam = Exam.objects.select_for_update().filter(
                pk=EXAM_ID, title=TITLE, mode=Exam.Mode.FIXED, published=True,
                question_count=9, total_points=25).first()
            if exam is None:
                raise CommandError("The expected published FA3 exam is unavailable.")
            invitations = list(Invitation.objects.select_for_update().filter(exam=exam).order_by("pk"))
            if any(inv.variant_status == Invitation.VariantStatus.GENERATING for inv in invitations):
                raise CommandError("A question set is being prepared; retry shortly.")
            attempts = list(Attempt.objects.select_for_update().filter(invitation__exam=exam).order_by("pk"))
            if any(attempt.status != Attempt.Status.DONE for attempt in attempts):
                raise CommandError("An exam attempt is active or awaiting grading; retry later.")
            if RegradeJob.objects.filter(attempt__invitation__exam=exam, status__in=(
                    RegradeJob.Status.PENDING, RegradeJob.Status.GRADING)).exists():
                raise CommandError("A regrade is running; retry after it finishes.")

            questions = list(Question.objects.select_for_update().filter(exam=exam, slot=SLOT).order_by("pk"))
            if len([q for q in questions if q.invitation_id is None]) != 1:
                raise CommandError("The FA3 question 2 template is missing or duplicated.")
            by_invitation = {q.invitation_id: q for q in questions if q.invitation_id is not None}
            if any(inv.variant_status == Invitation.VariantStatus.READY and inv.pk not in by_invitation
                   for inv in invitations):
                raise CommandError("A prepared question set is missing FA3 question 2.")

            old_content = (OLD_STEM, OLD_KEY, OLD_RUBRIC, OLD_MODEL)
            corrected_old_content = (OLD_STEM, CORRECTED_OLD_KEY, OLD_RUBRIC, OLD_MODEL)
            previous_grouped_content = tuple(PREVIOUS_GROUPED_ROW[field] for field in (
                "stem", "interaction", "rubric", "model_answer"))
            new_content = tuple(NEW_ROW[field] for field in ("stem", "interaction", "rubric", "model_answer"))
            accepted_content = (old_content, corrected_old_content, previous_grouped_content, new_content)
            for question in questions:
                content = (question.stem, question.interaction, question.rubric, question.model_answer)
                if question.kind != Question.Kind.CLOZE or question.points != 2 or content not in accepted_content:
                    raise CommandError(f"Unexpected FA3 question 2 data for question {question.pk}.")

            attempted = {attempt.invitation_id for attempt in attempts}
            changed = [q for q in questions if q.invitation_id not in attempted
                       and (q.stem, q.interaction, q.rubric, q.model_answer) != new_content]
            if options["apply"] and changed:
                for question in changed:
                    question.stem, question.interaction, question.rubric, question.model_answer = new_content
                Question.objects.bulk_update(changed, ["stem", "interaction", "rubric", "model_answer"], batch_size=200)
            label = "Applied" if options["apply"] else "Dry run"
            self.stdout.write(f"{label}: {len(changed)} unattempted question copies to update; "
                              f"{len(attempted)} attempted invitations left unchanged.")
