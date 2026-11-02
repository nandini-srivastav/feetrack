import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models

from fees import rules

ZERO = Decimal("0")


class Centre(models.Model):
    code = models.CharField(max_length=8, unique=True)  # used in receipt numbers, e.g. JPR2
    name = models.CharField(max_length=80)
    gstin = models.CharField(max_length=15, blank=True)

    def __str__(self):
        return self.name


class Batch(models.Model):
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="batches")
    name = models.CharField(max_length=80)
    course_start = models.DateField()
    course_end = models.DateField()

    class Meta:
        verbose_name_plural = "batches"
        unique_together = [("centre", "name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Student(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        WITHDRAWN = "withdrawn", "Withdrawn"

    student_id = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=80)
    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="students")
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="children")
    is_minor = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    withdrawn_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["student_id"]

    def __str__(self):
        return f"{self.name} ({self.student_id})"

    @property
    def centre(self):
        return self.batch.centre


class FeePlan(models.Model):
    student = models.OneToOneField(Student, on_delete=models.CASCADE, related_name="plan")
    total_fee = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    discount_reason = models.CharField(max_length=80, blank=True)

    @property
    def net_fee(self):
        return self.total_fee - self.discount

    def __str__(self):
        return f"Plan for {self.student}"


class Instalment(models.Model):
    plan = models.ForeignKey(FeePlan, on_delete=models.CASCADE, related_name="instalments")
    number = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # Denormalised sum of allocations; only fees.services writes it, inside
    # the same transaction as the Allocation rows.
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    cancelled = models.BooleanField(default=False)  # e.g. future instalments after a withdrawal

    class Meta:
        ordering = ["plan", "number"]
        unique_together = [("plan", "number")]
        constraints = [
            models.CheckConstraint(condition=models.Q(paid_amount__gte=0), name="instalment_paid_non_negative"),
            models.CheckConstraint(condition=models.Q(paid_amount__lte=models.F("amount")), name="instalment_not_overpaid"),
        ]

    def __str__(self):
        return f"{self.plan.student.student_id} #{self.number}"

    @property
    def balance(self):
        return self.amount - self.paid_amount

    def state(self, today):
        if self.cancelled:
            return rules.InstalmentState("cancelled", 0)
        return rules.instalment_state(self.amount, self.paid_amount, self.due_date, today)


class Payment(models.Model):
    class Method(models.TextChoices):
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        NETBANKING = "netbanking", "Net banking"
        BANK = "bank", "Bank transfer"
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"

    class Source(models.TextChoices):
        GATEWAY = "gateway", "Payment link"
        OFFLINE = "offline", "Recorded at centre"
        MATCHED = "matched", "Matched from queue"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=12, choices=Method.choices)
    source = models.CharField(max_length=12, choices=Source.choices)
    reference = models.CharField(max_length=64, blank=True)
    received_on = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_on", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_positive")]


class Allocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    instalment = models.ForeignKey(Instalment, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=10, decimal_places=2)


class ReceiptCounter(models.Model):
    """Gap-free receipt sequence per centre per financial year (row-locked)."""

    centre = models.ForeignKey(Centre, on_delete=models.PROTECT)
    financial_year = models.CharField(max_length=7)  # "2026-27"
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("centre", "financial_year")]


class Receipt(models.Model):
    number = models.CharField(max_length=32, unique=True)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    taxable_value = models.DecimalField(max_digits=10, decimal_places=2)
    cgst = models.DecimalField(max_digits=10, decimal_places=2)
    sgst = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    issued_at = models.DateTimeField(auto_now_add=True)


class PaymentLink(models.Model):
    """One link per instalment balance. The token is the only thing in the URL."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        USED = "used", "Used"
        VOID = "void", "Void"

    token = models.CharField(max_length=48, unique=True, editable=False)
    instalment = models.ForeignKey(Instalment, on_delete=models.CASCADE, related_name="links")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)


class GatewayEvent(models.Model):
    """Every webhook call, stored once by event_id so retries are harmless."""

    event_id = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=24, blank=True)  # applied / unmatched / failed / ignored


class UnmatchedPayment(models.Model):
    """Money we received but could not safely tie to an instalment.

    Nothing is marked paid from here until a person confirms the match.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MATCHED = "matched", "Matched"
        RETURNED = "returned", "Returned to payer"

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=12, choices=Payment.Method.choices)
    reference = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=120, blank=True)
    received_on = models.DateField()
    centre = models.ForeignKey(Centre, null=True, blank=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=160)
    suggested_student = models.ForeignKey(Student, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    suggestion_reason = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolved_payment = models.OneToOneField(Payment, null=True, blank=True, on_delete=models.SET_NULL)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    resolved_at = models.DateTimeField(null=True, blank=True)
    gateway_event = models.ForeignKey(GatewayEvent, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-received_on", "-id"]


class RefundPolicy(models.Model):
    version = models.CharField(max_length=16, unique=True)
    published_on = models.DateField()
    non_refundable_amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    payout_days = models.PositiveSmallIntegerField(default=10)
    summary = models.TextField()
    is_active = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "refund policies"

    def __str__(self):
        return f"Refund policy {self.version}"


class Refund(models.Model):
    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid out"

    student = models.OneToOneField(Student, on_delete=models.PROTECT, related_name="refund")
    policy = models.ForeignKey(RefundPolicy, on_delete=models.PROTECT)
    last_day = models.DateField()
    reason = models.CharField(max_length=80)
    breakdown = models.JSONField()  # full calculation, as shown on screen
    refund_due = models.DecimalField(max_digits=10, decimal_places=2)
    pay_by = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.APPROVED)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
