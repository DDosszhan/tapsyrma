"""Apply reviewed question-aligned rubrics to the two published fixed exams."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from exams.models import Attempt, Exam, Invitation, Question, RegradeJob


REVISIONS = {
    (13, 1): {
        "stem": "Explain the stored-program concept in a Von Neumann computer.",
        "old": [
            {"criterion": "Program instructions and data are stored in the same main memory.", "points": 1},
            {"criterion": "The processor fetches the stored instructions from memory for execution.", "points": 1},
        ],
        "new": [
            {"criterion": "Program instructions are stored in main memory; a statement that instructions and data share memory also earns this mark.", "points": 1},
            {"criterion": "Data is stored in the same main memory as program instructions; accept equivalent shared-memory wording. Fetch/execution detail is not required.", "points": 1},
        ],
        "old_model_answer": "Program instructions and data are stored in the same main memory. The processor fetches those instructions from memory for execution.",
        "new_model_answer": "Program instructions are stored in main memory together with the data they use; instructions and data share the same memory.",
    },
    (13, 4): {
        "stem": "State one function of each of the following components.\r\n\r\n1. Arithmetic and Logic Unit (ALU)\r\n2. Control Unit (CU)\r\n3. System clock",
        "old": [
            {"criterion": "ALU performs arithmetic or logic operations.", "points": 1},
            {"criterion": "CU generates control signals to coordinate execution; accept decoding instructions as an alternative.", "points": 1},
            {"criterion": "System clock generates regular pulses that synchronise processor operations.", "points": 1},
        ],
        "new": [
            {"criterion": "ALU performs arithmetic or logic operations.", "points": 1},
            {"criterion": "CU decodes instructions or directs/coordinates processor operations, including issuing control signals or directing data flow.", "points": 1},
            {"criterion": "The system clock provides regular timing signals or pulses that synchronise or pace processor operations.", "points": 1},
        ],
    },
    (13, 6): {
        "stem": "The Program Counter contains 240. The next sequential instruction is stored at address 241.\r\n\r\n(a) Using register-transfer notation, show how the CPU fetches the instruction at address 240 and updates the Program Counter. Use MEM[address] to represent the contents of a memory location. [3]\r\n\r\n(b) State what the Control Unit does with the fetched instruction during the decode stage. [1]",
        "old": [
            {"criterion": "MAR receives the original PC value: MAR ← PC.", "points": 1},
            {"criterion": "MDR receives the contents of memory at MAR: MDR ← MEM[MAR].", "points": 1},
            {"criterion": "CIR receives MDR and PC increments by one after the original PC value has been copied to MAR.", "points": 1},
            {"criterion": "The CU interprets the opcode to determine the operation to perform.", "points": 1},
        ],
        "new": [
            {"criterion": "MAR receives the original PC value (240): MAR ← PC, or equivalent.", "points": 1},
            {"criterion": "The instruction is read from memory at MAR into MDR, or an equivalent explicit memory-to-instruction-register transfer; CIR ← MDR alone without a memory read is insufficient.", "points": 1},
            {"criterion": "PC updates to the next address after the original address is used: PC ← PC + 1, or PC becomes 241.", "points": 1},
            {"criterion": "The CU decodes/interprets the instruction or opcode to determine the operation.", "points": 1},
        ],
    },
    (13, 11): {
        "stem": "A keyboard sends an interrupt request while the processor is executing an instruction. Assume the interrupt is enabled and is accepted at the next interrupt check.\r\n\r\nState when the processor checks for the interrupt. Then explain what the processor does to service it and resume the interrupted program.",
        "old": [
            {"criterion": "The processor checks for the interrupt after completing the current instruction, before fetching the next one.", "points": 1},
            {"criterion": "It saves the state needed to resume, such as PC and relevant registers, and runs the appropriate Interrupt Service Routine (ISR).", "points": 1},
            {"criterion": "When the ISR finishes, it restores the saved state and resumes the interrupted program.", "points": 1},
        ],
        "new": [
            {"criterion": "The interrupt is checked after the current instruction completes and before the next instruction is fetched.", "points": 1},
            {"criterion": "The processor runs the appropriate Interrupt Service Routine (ISR) or interrupt handler to service the request.", "points": 1},
            {"criterion": "It saves and later restores enough program state to resume the interrupted program; specific register names are not required.", "points": 1},
        ],
    },
    (14, 20): {
        "stem": "Describe how the processor handles an accepted interrupt and then continues the interrupted program. Refer to the Interrupt Service Routine (ISR).",
        "old": [
            {"criterion": "Saves the program context, including return address and relevant registers.", "points": 1},
            {"criterion": "Transfers control to and executes the appropriate ISR.", "points": 1},
            {"criterion": "Restores context and resumes the interrupted program.", "points": 1},
        ],
        "new": [
            {"criterion": "Saves enough current program state to resume later, such as PC/return address and relevant registers; exact register names are not required.", "points": 1},
            {"criterion": "Transfers control to and executes the appropriate ISR.", "points": 1},
            {"criterion": "Restores context and resumes the interrupted program.", "points": 1},
        ],
    },
}


class Command(BaseCommand):
    help = "Preview or apply reviewed FO/SOR rubrics to templates and all issued question copies."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the reviewed rubrics; default is a dry run.")

    def handle(self, *args, **options):
        with transaction.atomic():
            exams = list(Exam.objects.select_for_update().filter(pk__in={key[0] for key in REVISIONS}).order_by("pk"))
            if len(exams) != 2 or any(exam.mode != Exam.Mode.FIXED or not exam.published for exam in exams):
                raise CommandError("The expected published fixed exams are unavailable.")
            invitations = list(Invitation.objects.select_for_update().filter(exam__in=exams).order_by("pk"))
            if any(invitation.variant_status == Invitation.VariantStatus.GENERATING for invitation in invitations):
                raise CommandError("A question set is being prepared; run this command again shortly.")
            attempts = list(Attempt.objects.select_for_update().filter(invitation__exam__in=exams).order_by("pk"))
            if any(attempt.status in (Attempt.Status.ACTIVE, Attempt.Status.PENDING, Attempt.Status.GRADING)
                   for attempt in attempts):
                raise CommandError("An exam attempt is active or being graded; run this command again after it finishes.")
            if RegradeJob.objects.filter(attempt__invitation__exam__in=exams,
                                         status__in=(RegradeJob.Status.PENDING, RegradeJob.Status.GRADING)).exists():
                raise CommandError("A regrade is running; run this command again after it finishes.")

            questions = list(Question.objects.select_for_update().filter(
                exam__in=exams, slot__in={key[1] for key in REVISIONS}).order_by("pk"))
            by_key = {(q.exam_id, q.slot): [] for q in questions}
            for question in questions:
                key = (question.exam_id, question.slot)
                if key in REVISIONS:
                    by_key[key].append(question)
            if any(len([q for q in by_key.get(key, []) if q.invitation_id is None]) != 1 for key in REVISIONS):
                raise CommandError("A reviewed question template is missing or duplicated.")
            question_keys = {(q.invitation_id, q.slot) for q in questions if q.invitation_id is not None}
            for invitation in invitations:
                if invitation.variant_status == Invitation.VariantStatus.READY and any(
                        (invitation.pk, slot) not in question_keys
                        for exam_id, slot in REVISIONS if exam_id == invitation.exam_id):
                    raise CommandError(f"Prepared question set {invitation.pk} is missing a reviewed slot.")

            changes = []
            old_by_pk = {}
            for key, revision in REVISIONS.items():
                if sum(row["points"] for row in revision["old"]) != sum(row["points"] for row in revision["new"]):
                    raise CommandError(f"Reviewed marks do not match for exam {key[0]} slot {key[1]}.")
                for question in by_key.get(key, []):
                    if (question.kind != Question.Kind.OPEN or question.grading_mode != Question.GradingMode.AI
                            or question.stem != revision["stem"]
                            or question.points != sum(row["points"] for row in revision["new"])
                            or question.rubric not in (revision["old"], revision["new"])):
                        raise CommandError(f"Unexpected question data for exam {key[0]} slot {key[1]} question {question.pk}.")
                    if "new_model_answer" in revision and question.model_answer not in (
                            revision["old_model_answer"], revision["new_model_answer"]):
                        raise CommandError(f"Unexpected answer key for exam {key[0]} slot {key[1]} question {question.pk}.")
                    if question.rubric == revision["old"] or (
                            "new_model_answer" in revision and question.model_answer == revision["old_model_answer"]):
                        if question.rubric == revision["old"]:
                            old_by_pk[question.pk] = revision
                        question.rubric = revision["new"]
                        if "new_model_answer" in revision:
                            question.model_answer = revision["new_model_answer"]
                        changes.append(question)

            snapshots = 0
            for attempt in attempts:
                grades = dict(attempt.grades)
                for question_id in attempt.question_ids:
                    revision = old_by_pk.get(question_id)
                    if not revision:
                        continue
                    key = str(question_id)
                    if key in grades and "rubric_snapshot" not in grades[key]:
                        grades[key] = {**grades[key], "rubric_snapshot": revision["old"]}
                        if "old_model_answer" in revision:
                            grades[key]["model_answer_snapshot"] = revision["old_model_answer"]
                        snapshots += 1
                if options["apply"] and grades != attempt.grades:
                    attempt.grades = grades
                    attempt.save(update_fields=["grades"])
            if options["apply"] and changes:
                Question.objects.bulk_update(changes, ["rubric", "model_answer"], batch_size=200)
            status = "Applied" if options["apply"] else "Dry run"
            self.stdout.write(f"{status}: {len(changes)} question copies, {snapshots} saved-grade snapshots; no scores changed.")
