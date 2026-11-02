from django.conf import settings
from django.db import models


class DemoClock(models.Model):
    """Singleton row that lets the demo pretend 'today' is a fixed date.

    The brief's sample data is written as of 2 Nov 2026 ("23 days overdue",
    "due in 13 days"). Business logic never calls date.today() directly; it
    asks core.clock.today(), which honours this override when it is set.
    """

    simulated_date = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Clock: {self.simulated_date or 'real time'}"


class AuditLog(models.Model):
    """Append-only record of money-affecting and access-relevant actions."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=128, blank=True)
    data = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["action", "at"])]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} {self.target}"
