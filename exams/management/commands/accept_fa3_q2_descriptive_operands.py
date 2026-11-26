"""Credit clearly identified opcode and immediate operand in old FA3 answers."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.grading import grade_structured
from exams.management.commands.enable_fa3_q2_grouped_cloze import NEW_ROW, PREVIOUS_GROUPED_ROW
from exams.management.commands.repair_fa3_opcode_operand import (
    EXAM_ID, NEW_KEY as OLD_KEY, RUBRIC, SLOT, STEM, TITLE,
)
from exams.models import Attempt, Exam, GradeAppeal, Invitation, Question, RegradeJob


OLD_MODEL = "1. ADD #12\n2. Arithmetic"
NEW_KEY = {"answers": [OLD_KEY["answers"][0] + [
    "ADD is opcode  #12 is operand",
    "ADD is opcode, #12 is operand",
    "Opcode - ADD  Operand - #12",
    "Opcode ADD Operand #12",
    "Opcode: ADD. Operand:#12",
    "opcode is ADD operand is #12",
    "opcode is ADD; operand is #12",
], OLD_KEY["answers"][1]]}
REASON = "Accepted explicitly identified ADD opcode and #12 immediate operand in FA3 question 2."


class Command(BaseCommand):
    help = "Preview or credit unambiguous descriptive answers on attempted two-field FA3 question 2 copies."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Update eligible old answer keys and grades.")

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
                raise CommandError("An exam attempt is active or awaiting grading; retry later.")
            if RegradeJob.objects.filter(attempt__invitation__exam=exam, status__in=(
                    RegradeJob.Status.PENDING, RegradeJob.Status.GRADING)).exists():
                raise CommandError("A regrade is running; retry after it finishes.")
            for attempt in attempts:
                if attempt.score != sum(grade["score"] for grade in attempt.grades.values()):
                    raise CommandError(f"Attempt {attempt.pk} has an inconsistent saved total.")

            questions = list(Question.objects.select_for_update().filter(exam=exam, slot=SLOT).order_by("pk"))
            templates = [q for q in questions if q.invitation_id is None]
            grouped = [tuple(row[field] for field in (
                "stem", "interaction", "rubric", "model_answer"))
                for row in (PREVIOUS_GROUPED_ROW, NEW_ROW)]
            if (len(templates) != 1 or templates[0].kind != Question.Kind.CLOZE
                    or templates[0].points != 2
                    or tuple(getattr(templates[0], field) for field in (
                        "stem", "interaction", "rubric", "model_answer")) not in grouped):
                raise CommandError("The new three-field FA3 question 2 template is unavailable.")
            by_id = {q.pk: q for q in questions}
            appeals = {
                (attempt_id, question_id): status
                for attempt_id, question_id, status in GradeAppeal.objects.select_for_update().filter(
                    attempt__invitation__exam=exam, question__slot=SLOT).values_list(
                        "attempt_id", "question_id", "status")
            }
            changed_questions = []
            corrected_count = points_added = snapshot_count = pending_count = final_count = 0
            for attempt in attempts:
                copies = [by_id[pk] for pk in attempt.question_ids if pk in by_id]
                if len(copies) != 1 or copies[0].invitation_id != attempt.invitation_id:
                    raise CommandError(f"Attempt {attempt.pk} has no unique FA3 question 2 copy.")
                question = copies[0]
                if not isinstance(question.interaction, dict):
                    raise CommandError(f"Unexpected FA3 question 2 key for question {question.pk}.")
                if question.interaction.get("scoring_rule") == "grouped_cloze_v1":
                    if (question.kind != Question.Kind.CLOZE or question.points != 2
                            or tuple(getattr(question, field) for field in (
                                "stem", "interaction", "rubric", "model_answer")) not in grouped):
                        raise CommandError(f"Unexpected grouped FA3 question 2 data for question {question.pk}.")
                    continue
                if (question.kind != Question.Kind.CLOZE or question.points != 2
                        or question.stem != STEM or question.rubric != RUBRIC
                        or question.model_answer != OLD_MODEL
                        or question.interaction not in (OLD_KEY, NEW_KEY)):
                    raise CommandError(f"Unexpected old FA3 question 2 data for question {question.pk}.")
                appeal_status = appeals.get((attempt.pk, question.pk))
                if appeal_status in (GradeAppeal.Status.ACCEPTED, GradeAppeal.Status.REJECTED):
                    final_count += 1
                    continue
                if appeal_status == GradeAppeal.Status.PENDING:
                    pending_count += 1
                key = str(question.pk)
                old_grade = attempt.grades.get(key)
                if old_grade is None:
                    raise CommandError(f"Graded attempt {attempt.pk} is missing question 2's grade.")
                answer = attempt.answers.get(key, "")
                if question.interaction == NEW_KEY:
                    expected_grade = grade_structured(question, answer)
                    if (old_grade["score"], old_grade["criteria"]) != (
                            expected_grade["score"], expected_grade["criteria"]):
                        raise CommandError(f"Question 2's saved grade differs from its key in attempt {attempt.pk}.")
                    continue
                try:
                    base_grade = grade_structured(question, answer)
                    question.interaction = NEW_KEY
                    new_grade = grade_structured(question, answer)
                except (ValueError, TypeError, KeyError, AttributeError) as exc:
                    raise CommandError(f"Cannot check saved question 2 answer in attempt {attempt.pk}.") from exc
                if (old_grade["score"], old_grade["criteria"]) != (
                        base_grade["score"], base_grade["criteria"]):
                    raise CommandError(f"Question 2's saved grade differs from its key in attempt {attempt.pk}.")
                if new_grade["score"] < old_grade["score"]:
                    raise CommandError(f"Question 2 would lose points in attempt {attempt.pk}.")
                grades = dict(attempt.grades)
                if new_grade["score"] != old_grade["score"] or new_grade["criteria"] != old_grade["criteria"]:
                    grades[key] = {
                        **new_grade,
                        "interaction_snapshot": old_grade.get("interaction_snapshot", OLD_KEY),
                        "rubric_snapshot": old_grade.get("rubric_snapshot", RUBRIC),
                        "key_correction": {"old_grade": old_grade, "reason": REASON},
                    }
                    corrected_count += 1
                    points_added += new_grade["score"] - old_grade["score"]
                elif "interaction_snapshot" not in old_grade or "rubric_snapshot" not in old_grade:
                    grades[key] = {
                        "interaction_snapshot": OLD_KEY, "rubric_snapshot": RUBRIC, **old_grade,
                    }
                    snapshot_count += 1
                if options["apply"] and grades != attempt.grades:
                    attempt.grades = grades
                    attempt.score = sum(grade["score"] for grade in grades.values())
                    attempt.save(update_fields=["grades", "score"])
                changed_questions.append(question)
            if options["apply"] and changed_questions:
                Question.objects.bulk_update(changed_questions, ["interaction"], batch_size=200)
            label = "Applied" if options["apply"] else "Dry run"
            self.stdout.write(f"{label}: {len(changed_questions)} old question copies, "
                              f"{corrected_count} score corrections (+{points_added} points), "
                              f"{snapshot_count} saved-grade snapshots, {pending_count} pending appeals kept, "
                              f"{final_count} finalized appeals skipped.")
