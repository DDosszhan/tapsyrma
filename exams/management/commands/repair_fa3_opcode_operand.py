"""Accept unambiguous opcode, operand, and arithmetic-group answers in FA3."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.grading import grade_structured
from exams.models import Attempt, Exam, GradeAppeal, Invitation, Question, RegradeJob


EXAM_ID = 19
SLOT = 2
TITLE = "Formative Assessment 3 — Assembly Language"
STEM = ("Consider the instruction:\r\n\r\n```text\r\nADD #12\r\n```\r\n\r\nIdentify:"
        "\r\n\r\n- Opcode and operand (in that order, separated by one space): [[1]]"
        "\r\n- Instruction group: [[2]]")
OLD_KEY = {"answers": [["ADD #12"], ["Arithmetic", "Arithmetic operations"]]}
NEW_KEY = {"answers": [
    ["ADD #12", "ADD#12", "ADD - opcode, #12 - operand", "OPCODE IS ADD AND OPERAND IS #12"],
    ["Arithmetic", "Arithmetic operations", "Arithmetic operation", "Arithmetic operate",
     "Arthimetic", "Arthmetic", "Arithmetic opera"],
]}
RUBRIC = [
    {"criterion": "Blank 1: ADD #12", "points": 1},
    {"criterion": "Blank 2: Arithmetic", "points": 1},
]
REASON = "Accepted unambiguous opcode/operand notation and arithmetic-group spelling in FA3 question 2."


class Command(BaseCommand):
    help = "Preview or apply corrected FA3 question 2 answer key and saved scores."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Update the key and saved scores.")

    def handle(self, *args, **options):
        with transaction.atomic():
            exam = Exam.objects.select_for_update().filter(
                pk=EXAM_ID, title=TITLE, mode=Exam.Mode.FIXED, published=True).first()
            if exam is None:
                raise CommandError("The expected published FA3 exam is unavailable.")
            invitations = list(Invitation.objects.select_for_update().filter(exam=exam).order_by("pk"))
            if any(inv.variant_status == Invitation.VariantStatus.GENERATING for inv in invitations):
                raise CommandError("A question set is being prepared; retry shortly.")
            attempts = list(Attempt.objects.select_for_update().filter(invitation__exam=exam).order_by("pk"))
            if any(attempt.status != Attempt.Status.DONE for attempt in attempts):
                raise CommandError("An exam attempt is not fully graded; retry after it finishes.")
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
            for question in questions:
                if (question.kind != Question.Kind.CLOZE or question.points != 2 or question.stem != STEM
                        or question.rubric != RUBRIC or question.interaction not in (OLD_KEY, NEW_KEY)):
                    raise CommandError(f"Unexpected FA3 question 2 data for question {question.pk}.")

            changed = [question for question in questions if question.interaction == OLD_KEY]
            old_by_pk = {question.pk: question for question in changed}
            decided = set(GradeAppeal.objects.filter(question_id__in=old_by_pk).exclude(
                status=GradeAppeal.Status.PENDING).values_list("question_id", flat=True))
            corrections = points_added = snapshots = 0
            for attempt in attempts:
                matches = [old_by_pk[pk] for pk in attempt.question_ids if pk in old_by_pk]
                if len(matches) > 1:
                    raise CommandError(f"Attempt {attempt.pk} contains more than one question 2 copy.")
                if not matches:
                    continue
                question = matches[0]
                key = str(question.pk)
                old_grade = attempt.grades.get(key)
                if old_grade is None:
                    raise CommandError(f"Graded attempt {attempt.pk} is missing question 2's grade.")
                if attempt.score != sum(grade["score"] for grade in attempt.grades.values()):
                    raise CommandError(f"Attempt {attempt.pk} has an inconsistent saved total.")
                grades = dict(attempt.grades)
                if question.pk in decided:
                    new_grade = old_grade
                else:
                    question.interaction = NEW_KEY
                    try:
                        new_grade = grade_structured(question, attempt.answers.get(key, ""))
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        raise CommandError(f"Cannot check saved question 2 answer in attempt {attempt.pk}.") from exc
                if new_grade["score"] < old_grade["score"]:
                    raise CommandError(f"Question 2 would lose points in attempt {attempt.pk}.")
                if new_grade["criteria"] != old_grade["criteria"] or new_grade["score"] != old_grade["score"]:
                    grades[key] = {**new_grade, "interaction_snapshot": OLD_KEY, "rubric_snapshot": RUBRIC,
                                   "key_correction": {"old_grade": old_grade, "reason": REASON}}
                    corrections += 1
                    points_added += new_grade["score"] - old_grade["score"]
                elif "interaction_snapshot" not in old_grade or "rubric_snapshot" not in old_grade:
                    grades[key] = {"interaction_snapshot": OLD_KEY, "rubric_snapshot": RUBRIC, **old_grade}
                    snapshots += 1
                if options["apply"] and grades != attempt.grades:
                    attempt.grades = grades
                    attempt.score = sum(grade["score"] for grade in grades.values())
                    attempt.save(update_fields=["grades", "score"])

            if options["apply"] and changed:
                for question in changed:
                    question.interaction = NEW_KEY
                Question.objects.bulk_update(changed, ["interaction"], batch_size=200)
            label = "Applied" if options["apply"] else "Dry run"
            self.stdout.write(f"{label}: {len(changed)} question copies, {corrections} score corrections "
                              f"(+{points_added} points), {snapshots} saved-grade snapshots.")
