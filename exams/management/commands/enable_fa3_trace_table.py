"""Enable editable trace cells for future FA3 attempts only."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.models import Attempt, Exam, Invitation, Question


EXAM_ID = 19
TITLE = "Formative Assessment 3 — Assembly Language"
SLOT = 6
OLD_STEM = (
    "The following program processes values stored in memory.\r\n\r\n"
    "Initially, ACC = 0 and IX = 0.\r\n\r\n"
    "| Memory address | Contents |\r\n|---:|---:|\r\n| 300 | 4 |\r\n| 301 | 7 |\r\n"
    "| 302 | 10 |\r\n| 400 | 0 |\r\n\r\n"
    "```text\r\n        LDR #0\r\nLOOP:   LDX 300\r\n        ADD #2\r\n"
    "        STO 400\r\n        INC IX\r\n        CMP #9\r\n        JPN LOOP\r\n"
    "        END\r\n```\r\n\r\n"
    "**(a)** Complete one row for each loop iteration that actually executes. "
    "Record ACC, IX and memory location 400 **immediately after `JPN LOOP` executes**.\r\n\r\n"
    "| Iteration | ACC | IX | Contents of address 400 | Does JPN jump? |\r\n"
    "|---|---|---|---|---|\r\n| 1 | | | | |\r\n"
    "| 2, if executed | | | | |\r\n| 3, if executed | | | | |\r\n\r\n"
    "Leave any unused row empty.\r\n\r\n"
    "You may write each row as:\r\n\r\n"
    "`Iteration: ACC, IX, memory[400], Yes/No`\r\n\r\n"
    "**(b)** State which value `CMP #9` compares with 9, and explain why the program stops."
)
NEW_STEM = OLD_STEM.replace(
    "You may write each row as:\r\n\r\n"
    "`Iteration: ACC, IX, memory[400], Yes/No`\r\n\r\n", "")
OLD_LAYOUT = {}
NEW_LAYOUT = {"response_layout": "trace_table_v1"}


class Command(BaseCommand):
    help = "Preview or enable the editable FA3 trace table for unstarted attempts."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Switch the template and unstarted copies.")

    def handle(self, *args, **options):
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
                raise CommandError("An exam attempt is not fully graded; retry after it finishes.")
            attempted_invitations = {attempt.invitation_id for attempt in attempts}
            questions = list(Question.objects.select_for_update().filter(exam=exam, slot=SLOT).order_by("pk"))
            if len([q for q in questions if q.invitation_id is None]) != 1:
                raise CommandError("The FA3 question 6 template is missing or duplicated.")
            by_invitation = {q.invitation_id: q for q in questions if q.invitation_id is not None}
            if any(inv.variant_status == Invitation.VariantStatus.READY and inv.pk not in by_invitation
                   for inv in invitations):
                raise CommandError("A prepared question set is missing FA3 question 6.")
            for question in questions:
                if (question.kind != Question.Kind.OPEN or question.points != 6
                        or len(question.rubric) != 6
                        or (question.stem, question.interaction) not in (
                            (OLD_STEM, OLD_LAYOUT), (NEW_STEM, NEW_LAYOUT))):
                    raise CommandError(f"Unexpected FA3 question 6 data for question {question.pk}.")
            changed = [q for q in questions if q.invitation_id not in attempted_invitations
                       and q.interaction == OLD_LAYOUT]
            preserved = sum(q.invitation_id in attempted_invitations for q in questions)
            if options["apply"] and changed:
                for question in changed:
                    question.stem = NEW_STEM
                    question.interaction = NEW_LAYOUT
                Question.objects.bulk_update(changed, ["stem", "interaction"], batch_size=200)
            label = "Applied" if options["apply"] else "Dry run"
            self.stdout.write(f"{label}: {len(changed)} question copies to switch; "
                              f"{preserved} attempted copies preserved.")
