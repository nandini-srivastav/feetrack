"""Database-backed fee operations.

Every function that moves money runs in one transaction and locks the
rows it changes, so two payments arriving together cannot both pay the
same balance, and receipt numbers stay gap-free.
"""
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from core import audit
from core.money import format_inr
from fees import rules
from fees.models import (
    Allocation, FeePlan, GatewayEvent, Instalment, Payment, PaymentLink, Receipt, ReceiptCounter,
    Refund, RefundPolicy, Student, UnmatchedPayment,
)
from reminders import services as reminder_services

ZERO = Decimal("0")


class PaymentError(Exception):
    """A payment could not be applied as asked; nothing was changed."""


# ---------------------------------------------------------------- plans

@transaction.atomic
def create_plan(student: Student, total_fee: Decimal, due_dates: list[date], discount: Decimal = ZERO,
                discount_reason: str = "") -> FeePlan:
    if discount < 0 or discount >= total_fee:
        raise rules.RuleError("Discount must be between 0 and the total fee")
    plan = FeePlan.objects.create(student=student, total_fee=total_fee, discount=discount,
                                  discount_reason=discount_reason)
    amounts = rules.split_into_instalments(total_fee - discount, due_dates)
    for n, (due, amount) in enumerate(zip(due_dates, amounts), start=1):
        inst = Instalment.objects.create(plan=plan, number=n, due_date=due, amount=amount)
        reminder_services.schedule_for(inst)
    return plan


def open_instalments(student: Student, lock=False):
    qs = Instalment.objects.filter(plan__student=student, cancelled=False, paid_amount__lt=F("amount"))
    if lock:
        qs = qs.select_for_update()
    return list(qs.order_by("due_date", "number"))


def ledger_summary(student: Student, today: date) -> dict:
    insts = list(Instalment.objects.filter(plan__student=student).order_by("number"))
    live = [i for i in insts if not i.cancelled]
    overdue = [i for i in live if i.state(today).is_overdue]
    worst = max((i.state(today).days_overdue for i in overdue), default=0)
    return {
        "instalments": insts,
        "total": sum((i.amount for i in live), ZERO),
        "paid": sum((i.paid_amount for i in insts), ZERO),
        "balance": sum((i.balance for i in live), ZERO),
        "overdue_amount": sum((i.balance for i in overdue), ZERO),
        "max_days_overdue": worst,
        "bucket": rules.ageing_bucket(worst),
        "next_due": next((i for i in live if i.balance > 0 and not i.state(today).is_overdue), None),
    }


# ------------------------------------------------------------- receipts

def _next_receipt_number(centre, on: date) -> str:
    fy = rules.financial_year(on)
    counter, _ = ReceiptCounter.objects.get_or_create(centre=centre, financial_year=fy)
    counter = ReceiptCounter.objects.select_for_update().get(pk=counter.pk)
    counter.last_number += 1
    counter.save(update_fields=["last_number"])
    return f"{centre.code}-{fy[2:]}-{counter.last_number:06d}"


def _issue_receipt(payment: Payment) -> Receipt:
    split = rules.gst_split(payment.amount)
    return Receipt.objects.create(
        number=_next_receipt_number(payment.student.batch.centre, payment.received_on),
        payment=payment, taxable_value=split.taxable, cgst=split.cgst, sgst=split.sgst, total=split.total,
    )


# ------------------------------------------------------------- payments

@transaction.atomic
def apply_payment(*, student: Student, amount: Decimal, method: str, source: str, received_on: date,
                  reference: str = "", recorded_by=None, instalment: Instalment | None = None) -> Payment:
    """Record money against a student's instalments and issue a receipt.

    With `instalment`, the money may only go to that instalment (a payment
    link is for one instalment). Without it, oldest balance first.
    Raises PaymentError rather than accept an over-payment.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise PaymentError("Amount must be more than zero.")
    if student.status != Student.Status.ACTIVE:
        raise PaymentError("This student has withdrawn; payments go through the refund record.")

    open_items = open_instalments(student, lock=True)
    if instalment is not None:
        open_items = [i for i in open_items if i.pk == instalment.pk]
        if not open_items:
            raise PaymentError("That instalment is already paid or cancelled.")

    allocations, leftover = rules.allocate_oldest_first(
        amount, [rules.OpenInstalment(i.pk, i.due_date, i.balance) for i in open_items])
    if leftover > 0:
        raise PaymentError(f"Payment is {format_inr(leftover)} more than the balance owed.")

    payment = Payment.objects.create(student=student, amount=amount, method=method, source=source,
                                     reference=reference, received_on=received_on, recorded_by=recorded_by)
    by_id = {i.pk: i for i in open_items}
    for inst_id, part in allocations:
        inst = by_id[inst_id]
        Allocation.objects.create(payment=payment, instalment=inst, amount=part)
        inst.paid_amount += part
        inst.save(update_fields=["paid_amount"])
        if inst.balance == 0:
            # The client's rule: reminders stop the moment someone pays.
            reminder_services.cancel_for(inst, reason="Paid in full")
            PaymentLink.objects.filter(instalment=inst, status=PaymentLink.Status.ACTIVE).update(
                status=PaymentLink.Status.USED)
        else:
            # Part payment: any open link is for the old amount, so void it.
            PaymentLink.objects.filter(instalment=inst, status=PaymentLink.Status.ACTIVE).update(
                status=PaymentLink.Status.VOID)

    receipt = _issue_receipt(payment)
    reminder_services.send_receipt_message(payment, receipt)
    audit.record(recorded_by, "payment.applied", student.student_id, amount=amount, source=source,
                 receipt=receipt.number, allocations=[(str(by_id[k]), str(v)) for k, v in allocations])
    return payment


def get_or_create_link(instalment: Instalment) -> PaymentLink:
    if instalment.cancelled or instalment.balance <= 0:
        raise PaymentError("Nothing is owed on this instalment.")
    link = instalment.links.filter(status=PaymentLink.Status.ACTIVE, amount=instalment.balance).first()
    if link:
        return link
    instalment.links.filter(status=PaymentLink.Status.ACTIVE).update(status=PaymentLink.Status.VOID)
    return PaymentLink.objects.create(instalment=instalment, amount=instalment.balance)


# ------------------------------------------------------ gateway webhook

def sign_payload(body: bytes) -> str:
    return hmac.new(settings.GATEWAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str) -> bool:
    if not settings.GATEWAY_WEBHOOK_SECRET or not signature:
        return False
    return hmac.compare_digest(sign_payload(body), signature)


@dataclass
class GatewayOutcome:
    outcome: str  # applied | unmatched | failed | duplicate | blocked
    payment: Payment | None = None
    unmatched: UnmatchedPayment | None = None
    message: str = ""


def process_gateway_event(payload: dict) -> GatewayOutcome:
    """Handle one 'payment captured/failed' event from the gateway.

    Safe to call twice with the same event_id (gateways retry). Anything
    that does not line up exactly with an active link goes to the
    unmatched queue; nothing is guessed.
    """
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id:
        raise PaymentError("Missing event_id")

    with transaction.atomic():
        event, created = GatewayEvent.objects.select_for_update().get_or_create(
            event_id=event_id, defaults={"payload": payload})
        if not created:
            return GatewayOutcome("duplicate", message=f"Already processed: {event.outcome}")

        status = payload.get("status")
        if status != "captured":
            event.outcome = "failed"
            event.save(update_fields=["outcome"])
            return GatewayOutcome("failed", message="Payment was not completed.")

        try:
            amount = Decimal(str(payload["amount"]))
            paid_on = date.fromisoformat(payload["paid_on"])
        except (KeyError, ValueError, ArithmeticError):
            event.outcome = "failed"
            event.save(update_fields=["outcome"])
            raise PaymentError("Malformed amount or date")

        method = payload.get("method", "upi")
        reference = str(payload.get("reference", ""))[:64]
        link = PaymentLink.objects.select_related("instalment__plan__student__batch__centre").filter(
            token=payload.get("link_token", "")).first()

        reason = None
        if link is None:
            reason = "Payment link not recognised"
        elif link.status != PaymentLink.Status.ACTIVE:
            reason = f"Link already {link.get_status_display().lower()}"
        elif amount != link.amount:
            reason = f"Amount {format_inr(amount)} differs from the link amount {format_inr(link.amount)}"
        elif amount > link.instalment.balance:
            reason = "Amount is more than the instalment balance"

        if reason is None:
            try:
                payment = apply_payment(student=link.instalment.plan.student, amount=amount, method=method,
                                        source=Payment.Source.GATEWAY, received_on=paid_on, reference=reference,
                                        instalment=link.instalment)
            except PaymentError as exc:
                reason = str(exc)
            else:
                event.outcome = "applied"
                event.save(update_fields=["outcome"])
                return GatewayOutcome("applied", payment=payment)

        student = link.instalment.plan.student if link else None
        unmatched = UnmatchedPayment.objects.create(
            amount=amount, method=method, reference=reference, note=str(payload.get("note", ""))[:120],
            received_on=paid_on, centre=student.batch.centre if student else None, reason=reason,
            suggested_student=student, suggestion_reason="Paid through this student's link" if student else "",
            gateway_event=event)
        event.outcome = "unmatched"
        event.save(update_fields=["outcome"])
        audit.record(None, "payment.unmatched", reference or event_id, amount=amount, reason=reason)
        return GatewayOutcome("unmatched", unmatched=unmatched, message=reason)


# ------------------------------------------------------ unmatched queue

def suggest_match(unmatched: UnmatchedPayment) -> tuple[Student | None, str]:
    """Cheap, explainable suggestion: a student name in the payer's note
    whose open balance equals the amount. Never applied automatically."""
    words = {w for w in re.split(r"[^a-z]+", (unmatched.note or "").lower()) if len(w) >= 3}
    if not words:
        return None, ""
    candidates = Student.objects.filter(status=Student.Status.ACTIVE)
    if unmatched.centre_id:
        candidates = candidates.filter(batch__centre_id=unmatched.centre_id)
    for s in candidates.select_related("batch"):
        first = s.name.split()[0].lower()
        if first in words:
            balances = [i.balance for i in open_instalments(s)]
            if unmatched.amount in balances or unmatched.amount == sum(balances, ZERO):
                return s, f"Name “{s.name.split()[0]}” in note and amount equals an open balance"
    return None, ""


@transaction.atomic
def resolve_unmatched(unmatched: UnmatchedPayment, student: Student, user) -> Payment:
    unmatched = UnmatchedPayment.objects.select_for_update().get(pk=unmatched.pk)
    if unmatched.status != UnmatchedPayment.Status.OPEN:
        raise PaymentError("This payment has already been resolved.")
    payment = apply_payment(student=student, amount=unmatched.amount, method=unmatched.method,
                            source=Payment.Source.MATCHED, received_on=unmatched.received_on,
                            reference=unmatched.reference, recorded_by=user)
    unmatched.status = UnmatchedPayment.Status.MATCHED
    unmatched.resolved_payment = payment
    unmatched.resolved_by = user
    unmatched.resolved_at = timezone.now()
    unmatched.save()
    audit.record(user, "unmatched.resolved", student.student_id, amount=unmatched.amount, unmatched=unmatched.pk)
    return payment


# --------------------------------------------------------------- refunds

def active_policy() -> RefundPolicy:
    policy = RefundPolicy.objects.filter(is_active=True).order_by("-published_on").first()
    if policy is None:
        raise rules.RuleError("No refund policy is published. Publish one before calculating refunds.")
    return policy


def preview_refund(student: Student, last_day: date, request_date: date) -> tuple[rules.RefundResult, RefundPolicy]:
    policy = active_policy()
    plan = student.plan
    paid = Instalment.objects.filter(plan=plan).aggregate(s=Sum("paid_amount"))["s"] or ZERO
    result = rules.calculate_refund(
        net_fee=plan.net_fee, amount_paid=paid, course_start=student.batch.course_start,
        course_end=student.batch.course_end, last_day=last_day, request_date=request_date,
        non_refundable=policy.non_refundable_amount, payout_days=policy.payout_days)
    return result, policy


@transaction.atomic
def approve_refund(student: Student, last_day: date, request_date: date, reason: str, user) -> Refund:
    student = Student.objects.select_for_update().get(pk=student.pk)
    if student.status != Student.Status.ACTIVE:
        raise PaymentError("This student has already withdrawn.")
    result, policy = preview_refund(student, last_day, request_date)
    refund = Refund.objects.create(student=student, policy=policy, last_day=last_day, reason=reason,
                                   breakdown=result.as_dict(), refund_due=result.refund_due,
                                   pay_by=result.pay_by, approved_by=user)
    student.status = Student.Status.WITHDRAWN
    student.withdrawn_on = last_day
    student.save(update_fields=["status", "withdrawn_on"])
    for inst in Instalment.objects.filter(plan__student=student, cancelled=False):
        if inst.balance > 0:
            inst.cancelled = True
            inst.save(update_fields=["cancelled"])
            reminder_services.cancel_for(inst, reason="Student withdrew")
            inst.links.filter(status=PaymentLink.Status.ACTIVE).update(status=PaymentLink.Status.VOID)
    audit.record(user, "refund.approved", student.student_id, refund_due=result.refund_due,
                 policy=policy.version, breakdown=json.dumps(result.as_dict()))
    return refund
