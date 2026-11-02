from django.db import models


class Reminder(models.Model):
    """A scheduled message about one instalment.

    Rows are created when an instalment is created (one per offset). The
    dispatcher sends due rows; any payment that clears the balance cancels
    every row still scheduled for that instalment, in the same transaction.
    """

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        SENT = "sent", "Sent"
        CANCELLED = "cancelled", "Cancelled"

    instalment = models.ForeignKey("fees.Instalment", on_delete=models.CASCADE, related_name="reminders")
    offset_days = models.SmallIntegerField()
    send_on = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["send_on"]
        unique_together = [("instalment", "offset_days")]

    def label(self):
        if self.offset_days < 0:
            return f"{-self.offset_days} days before"
        if self.offset_days == 0:
            return "Due day"
        return f"{self.offset_days} days after"


class OutboundMessage(models.Model):
    """The mock SMS/WhatsApp outbox. In production this row would be handed
    to a provider; here it is the record that a message 'went out'."""

    class Kind(models.TextChoices):
        OTP = "otp", "OTP"
        REMINDER = "reminder", "Reminder"
        RECEIPT = "receipt", "Receipt"

    to_phone = models.CharField(max_length=15)
    language = models.CharField(max_length=2)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    body = models.TextField()
    reminder = models.ForeignKey(Reminder, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
