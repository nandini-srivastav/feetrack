"""Score students and turn high scores into counsellor call tasks.

Guardrails from the brief, enforced here and tested:
  * a flag only creates a CounsellorTask: no fee change, no parent message;
  * a student with neither attendance nor test data is not flagged;
  * after a counsellor marks a flag false, the student is not re-flagged
    for 30 days unless the score rises by at least 0.10.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core import audit
from fees.models import Student
from risk import features, model
from risk.models import CounsellorTask, RiskAssessment

QUIET_DAYS_AFTER_FALSE_FLAG = 30
RISE_TO_OVERRIDE_QUIET = 0.10


@transaction.atomic
def assess(student, today):
    row = features.build(student, today)
    p, contributions = model.score(row)
    assessment = RiskAssessment.objects.create(
        student=student, score=Decimal(f"{p:.3f}"), features={**row.values, "missing": row.missing},
        contributions=contributions, model_version=model.version(), assessed_on=today)

    decision = _decide(student, p, row, today)
    if decision == "flag":
        CounsellorTask.objects.create(student=student, assessment=assessment)
        audit.record(None, "risk.flagged", student.student_id, score=f"{p:.3f}", model=model.version())
    return assessment, decision


def _decide(student, p, row, today):
    if p < model.threshold():
        return "below_threshold"
    if "attendance" in row.missing and "tests" in row.missing:
        return "insufficient_data"
    if student.counsellor_tasks.filter(status=CounsellorTask.Status.OPEN).exists():
        return "already_open"
    last_false = (student.counsellor_tasks.filter(status=CounsellorTask.Status.FALSE_FLAG)
                  .select_related("assessment").order_by("-closed_at").first())
    if last_false and last_false.closed_at and \
            (today - timezone.localtime(last_false.closed_at).date()) < timedelta(days=QUIET_DAYS_AFTER_FALSE_FLAG):
        if p < float(last_false.assessment.score) + RISE_TO_OVERRIDE_QUIET:
            return "quiet_after_false_flag"
    return "flag"


def score_all(today):
    counts = {}
    for s in Student.objects.filter(status=Student.Status.ACTIVE).select_related("batch"):
        _, decision = assess(s, today)
        counts[decision] = counts.get(decision, 0) + 1
    return counts


@transaction.atomic
def close_task(task, *, user, false_flag: bool, outcome: str = "", notes: str = ""):
    task = CounsellorTask.objects.select_for_update().get(pk=task.pk)
    if task.status != CounsellorTask.Status.OPEN:
        raise ValueError("This task is already closed.")
    task.status = CounsellorTask.Status.FALSE_FLAG if false_flag else CounsellorTask.Status.DONE
    task.outcome = "Marked as false flag" if false_flag else outcome
    task.notes = notes
    task.closed_by = user
    task.closed_at = timezone.now()
    task.save()
    audit.record(user, "risk.false_flag" if false_flag else "risk.task_done", task.student.student_id,
                 score=task.assessment.score)
    return task
