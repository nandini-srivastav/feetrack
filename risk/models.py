from django.conf import settings
from django.db import models


class RiskAssessment(models.Model):
    """One scoring run for one student, with the reasons behind it."""

    student = models.ForeignKey("fees.Student", on_delete=models.CASCADE, related_name="risk_assessments")
    score = models.DecimalField(max_digits=4, decimal_places=3)
    features = models.JSONField()       # raw inputs, for audit
    contributions = models.JSONField()  # [{"feature","label","value","impact"}] sorted by impact
    model_version = models.CharField(max_length=16)
    assessed_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-assessed_on", "-id"]


class CounsellorTask(models.Model):
    """The ONLY thing a risk flag is allowed to create.

    It never touches fees and never messages a parent. Counsellors close it
    with an outcome, or mark the flag false; false flags are kept as labels
    for the next model retrain.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Call done"
        FALSE_FLAG = "false_flag", "False flag"

    student = models.ForeignKey("fees.Student", on_delete=models.CASCADE, related_name="counsellor_tasks")
    assessment = models.ForeignKey(RiskAssessment, on_delete=models.PROTECT, related_name="tasks")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    outcome = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["student"], condition=models.Q(status="open"),
                                    name="one_open_task_per_student"),
        ]
