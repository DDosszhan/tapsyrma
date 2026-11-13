"""Accept bracket notation in the published SOR register-transfer cloze."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.grading import grade_structured
from exams.models import Attempt, Exam, GradeAppeal, Invitation, Question, RegradeJob


EXAM_ID = 14
SLOT = 14
STEM = ("Complete the register transfer statements for the fetch stage.\r\n"
        "M[address] means the contents of that memory location.\r\n\r\n"
        "MAR <- [[1]]\r\nMDR <- M[[[2]]]\r\nCIR <- [[3]]\r\nPC <- PC + 1")
OLD_KEY = {"answers": [["PC"], ["MAR"], ["MDR"]]}
NEW_KEY = {"answers": [["PC", "[PC]"], ["MAR", "[MAR]"], ["MDR", "[MDR]"]]}
OLD_RUBRIC = [
    {"criterion": "Blank 1: PC", "points": 1},
    {"criterion": "Blank 2: MAR", "points": 1},
    {"criterion": "Blank 3: MDR", "points": 1},
]
NEW_RUBRIC = [
    {"criterion": "Blank 1: PC (also accept [PC])", "points": 1},
    {"criterion": "Blank 2: MAR (also accept [MAR])", "points": 1},
    {"criterion": "Blank 3: MDR (also accept [MDR])", "points": 1},
]
REASON = "Accepted bracketed register notation in SOR question 14."


class Command(BaseCommand):
    help = "Preview or apply bracketed register answers to SOR question 14."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Update the answer key and saved scores.")

    def handle(self, *args, **options):
        with transaction.atomic():
            exam = Exam.objects.select_for_update().filter(pk=EXAM_ID, mode=Exam.Mode.FIXED, published=True).first()
            if not exam:
                raise CommandError("The expected published fixed SOR exam is unavailable.")
            invitations = list(Invitation.objects.select_for_update().filter(exam=exam).order_by("pk"))
            if any(inv.variant_status == Invitation.VariantStatus.GENERATING for inv in invitations):
                raise CommandError("A question set is being prepared; retry shortly.")
            attempts = list(Attempt.objects.select_for_update().filter(invitation__exam=exam).order_by("pk"))
            if any(attempt.status in (Attempt.Status.PENDING, Attempt.Status.GRADING)
                   for attempt in attempts):
                raise CommandError("An exam attempt is being graded; retry after it finishes.")
            if RegradeJob.objects.filter(attempt__invitation__exam=exam, status__in=(
                    RegradeJob.Status.PENDING, RegradeJob.Status.GRADING)).exists():
                raise CommandError("A regrade is running; retry after it finishes.")

            questions = list(Question.objects.select_for_update().filter(exam=exam, slot=SLOT).order_by("pk"))
            if len([q for q in questions if q.invitation_id is None]) != 1:
                raise CommandError("The SOR question 14 template is missing or duplicated.")
            by_invitation = {q.invitation_id: q for q in questions if q.invitation_id is not None}
            if any(inv.variant_status == Invitation.VariantStatus.READY and inv.pk not in by_invitation
                   for inv in invitations):
                raise CommandError("A prepared SOR question set is missing question 14.")
            for question in questions:
                if (question.kind != Question.Kind.CLOZE or question.points != 3 or question.stem != STEM
                        or (question.interaction, question.rubric) not in (
                            (OLD_KEY, OLD_RUBRIC), (NEW_KEY, NEW_RUBRIC))):
                    raise CommandError(f"Unexpected SOR question 14 data for question {question.pk}.")

            changed = [q for q in questions if q.interaction == OLD_KEY]
            old_by_pk = {q.pk: q for q in changed}
            decided = set(GradeAppeal.objects.filter(question_id__in=old_by_pk).exclude(
                status=GradeAppeal.Status.PENDING).values_list("question_id", flat=True))
            corrections = 0
            points_added = 0
            snapshots = 0
            for attempt in attempts:
                question = next((old_by_pk[pk] for pk in attempt.question_ids if pk in old_by_pk), None)
                if not question:
                    continue
                key = str(question.pk)
                old_grade = attempt.grades.get(key)
                if old_grade is None:
                    if attempt.status == Attempt.Status.DONE:
                        raise CommandError(f"Graded attempt {attempt.pk} is missing question 14's grade.")
                    continue
                grades = dict(attempt.grades)
                if question.pk in decided:
                    new_grade = old_grade
                else:
                    question.interaction = NEW_KEY
                    try:
                        new_grade = grade_structured(question, attempt.answers.get(key, ""))
                    except (ValueError, TypeError, KeyError, AttributeError) as exc:
                        raise CommandError(f"Cannot check saved question 14 answer in attempt {attempt.pk}.") from exc
                if new_grade["score"] < old_grade["score"]:
                    raise CommandError(f"Question 14 would lose points in attempt {attempt.pk}.")
                if new_grade["criteria"] != old_grade["criteria"] or new_grade["score"] != old_grade["score"]:
                    corrected = {**new_grade, "key_correction": {"old_grade": old_grade, "reason": REASON}}
                    grades[key] = corrected
                    corrections += 1
                    points_added += corrected["score"] - old_grade["score"]
                elif "rubric_snapshot" not in old_grade or "interaction_snapshot" not in old_grade:
                    grades[key] = {"rubric_snapshot": OLD_RUBRIC, "interaction_snapshot": OLD_KEY, **old_grade}
                    snapshots += 1
                if options["apply"] and grades != attempt.grades:
                    if attempt.score != sum(row["score"] for row in attempt.grades.values()):
                        raise CommandError(f"Attempt {attempt.pk} has an inconsistent saved total.")
                    attempt.grades = grades
                    attempt.score = sum(row["score"] for row in grades.values())
                    attempt.save(update_fields=["grades", "score"])

            if options["apply"] and changed:
                for question in changed:
                    question.interaction = NEW_KEY
                    question.rubric = NEW_RUBRIC
                Question.objects.bulk_update(changed, ["interaction", "rubric"], batch_size=200)
            status = "Applied" if options["apply"] else "Dry run"
            self.stdout.write(f"{status}: {len(changed)} question copies, {corrections} score corrections "
                              f"(+{points_added} points), {snapshots} saved-grade snapshots.")
