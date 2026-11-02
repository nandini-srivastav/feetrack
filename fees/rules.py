"""Pure fee rules: no database, no clock, no Django.

Everything money-related that a reviewer or auditor might question lives
here, so it can be unit-tested exhaustively and read in one place. Amounts
are Decimal rupees. Dates are datetime.date. 'today' is always passed in.

Assumptions (all labelled in the TDD):
  A1  Fees are GST-inclusive at 18% (9% CGST + 9% SGST, intra-state).
  A2  A plan's net fee is split into equal instalments; any rounding
      remainder goes on the last instalment.
  A3  A payment pays the oldest unpaid instalment first.
  A4  Refund = amount paid - (net fee x days used / course days)
      - non-refundable items, never below zero; days are counted
      inclusively from course start to the last day attended.
  A5  Refunds are rounded to the nearest rupee, half up.
  A6  Reminders go 3 days before, on the due date, and 7, 14 and 30 days
      after, for as long as a balance remains.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
PAISA = Decimal("0.01")
RUPEE = Decimal("1")

GST_RATE = Decimal("18")
REMINDER_OFFSETS = (-3, 0, 7, 14, 30)
AGEING_BUCKETS = ((1, 30, "1–30"), (31, 60, "31–60"), (61, 90, "61–90"), (91, None, "90+"))


class RuleError(ValueError):
    """Raised when inputs would produce a nonsensical money result."""


# ---------------------------------------------------------------- plans ---

def split_into_instalments(net_fee: Decimal, due_dates: list[date]) -> list[Decimal]:
    """Split a net fee into len(due_dates) whole-rupee instalments (A2)."""
    net_fee = Decimal(net_fee)
    if not due_dates:
        raise RuleError("A plan needs at least one due date")
    if net_fee <= 0:
        raise RuleError("Net fee must be positive")
    if list(due_dates) != sorted(due_dates):
        raise RuleError("Due dates must be in order")
    n = len(due_dates)
    base = (net_fee / n).quantize(RUPEE, rounding="ROUND_FLOOR")
    amounts = [base] * n
    amounts[-1] = net_fee - base * (n - 1)
    return amounts


# --------------------------------------------------------- instalments ---

@dataclass(frozen=True)
class InstalmentState:
    status: str          # paid | part_paid_overdue | overdue | part_paid | due_soon | upcoming | cancelled
    days_overdue: int    # 0 unless overdue
    days_until_due: int = 0

    @property
    def is_overdue(self):
        return self.days_overdue > 0


def instalment_state(amount: Decimal, paid: Decimal, due: date, today: date, due_soon_days: int = 14) -> InstalmentState:
    balance = Decimal(amount) - Decimal(paid)
    if balance <= 0:
        return InstalmentState("paid", 0)
    if today > due:
        days = (today - due).days
        return InstalmentState("part_paid_overdue" if paid > 0 else "overdue", days)
    until = (due - today).days
    if paid > 0:
        return InstalmentState("part_paid", 0, until)
    if until <= due_soon_days:
        return InstalmentState("due_soon", 0, until)
    return InstalmentState("upcoming", 0, until)


def ageing_bucket(days_overdue: int) -> str | None:
    if days_overdue <= 0:
        return None
    for low, high, label in AGEING_BUCKETS:
        if days_overdue >= low and (high is None or days_overdue <= high):
            return label
    return None  # unreachable


# ---------------------------------------------------------- allocation ---

@dataclass(frozen=True)
class OpenInstalment:
    key: object          # instalment id, opaque to this module
    due_date: date
    balance: Decimal


def allocate_oldest_first(amount: Decimal, open_items: list[OpenInstalment]) -> tuple[list[tuple[object, Decimal]], Decimal]:
    """Apply a payment to the oldest open balances first (A3).

    Returns ([(key, amount_applied), ...], leftover). A leftover > 0 means
    the payment is larger than everything owed; the caller must not
    silently absorb it.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise RuleError("Payment amount must be positive")
    remaining = amount
    result = []
    for item in sorted(open_items, key=lambda i: i.due_date):
        if remaining <= 0:
            break
        if item.balance <= 0:
            continue
        applied = min(item.balance, remaining)
        result.append((item.key, applied))
        remaining -= applied
    return result, remaining


# ----------------------------------------------------------------- GST ---

@dataclass(frozen=True)
class GstSplit:
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    total: Decimal


def gst_split(total_inclusive: Decimal, rate: Decimal = GST_RATE) -> GstSplit:
    """Back out GST from an inclusive amount so the parts add up exactly (A1)."""
    total = Decimal(total_inclusive).quantize(PAISA, rounding=ROUND_HALF_UP)
    taxable = (total * 100 / (100 + rate)).quantize(PAISA, rounding=ROUND_HALF_UP)
    tax = total - taxable
    cgst = (tax / 2).quantize(PAISA, rounding=ROUND_HALF_UP)
    sgst = tax - cgst
    return GstSplit(taxable, cgst, sgst, total)


def financial_year(d: date) -> str:
    """Indian FY: 1 Apr – 31 Mar. 2 Nov 2026 -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


# -------------------------------------------------------------- refund ---

@dataclass(frozen=True)
class RefundResult:
    course_days: int
    days_used: int
    net_fee: Decimal
    fee_for_days_used: Decimal
    non_refundable: Decimal
    amount_retained: Decimal
    amount_paid: Decimal
    refund_due: Decimal
    still_owed: Decimal
    pay_by: date
    notes: list[str] = field(default_factory=list)

    def as_dict(self):
        return {
            "course_days": self.course_days,
            "days_used": self.days_used,
            "net_fee": str(self.net_fee),
            "fee_for_days_used": str(self.fee_for_days_used),
            "non_refundable": str(self.non_refundable),
            "amount_retained": str(self.amount_retained),
            "amount_paid": str(self.amount_paid),
            "refund_due": str(self.refund_due),
            "still_owed": str(self.still_owed),
            "pay_by": self.pay_by.isoformat(),
            "notes": list(self.notes),
        }


def calculate_refund(*, net_fee: Decimal, amount_paid: Decimal, course_start: date, course_end: date,
                     last_day: date, request_date: date, non_refundable: Decimal = ZERO,
                     payout_days: int = 10) -> RefundResult:
    """Pro-rata refund when a student leaves (A4, A5).

    The 2024 coaching guidelines require a pro-rata refund of the
    remaining period, paid within 10 days of the request.
    """
    net_fee, amount_paid, non_refundable = Decimal(net_fee), Decimal(amount_paid), Decimal(non_refundable)
    if course_end < course_start:
        raise RuleError("Course end is before course start")
    if net_fee < 0 or amount_paid < 0 or non_refundable < 0:
        raise RuleError("Amounts cannot be negative")
    if last_day > request_date:
        raise RuleError("Last day attended cannot be after the date of the request")

    notes = []
    course_days = (course_end - course_start).days + 1
    if last_day < course_start:
        days_used = 0
        notes.append("Left before the course started: no days charged.")
    elif last_day >= course_end:
        days_used = course_days
        notes.append("Course already complete: full fee retained.")
    else:
        days_used = (last_day - course_start).days + 1

    fee_used = (net_fee * days_used / course_days).quantize(RUPEE, rounding=ROUND_HALF_UP)
    retained = min(net_fee, fee_used + non_refundable)
    refund = max(ZERO, amount_paid - retained)
    owed = max(ZERO, retained - amount_paid)
    if owed > 0:
        notes.append("Fee for days used is more than the amount paid, so there is nothing to refund.")

    return RefundResult(
        course_days=course_days,
        days_used=days_used,
        net_fee=net_fee,
        fee_for_days_used=fee_used,
        non_refundable=non_refundable,
        amount_retained=retained,
        amount_paid=amount_paid,
        refund_due=refund.quantize(RUPEE, rounding=ROUND_HALF_UP),
        still_owed=owed.quantize(RUPEE, rounding=ROUND_HALF_UP),
        pay_by=request_date + timedelta(days=payout_days),
        notes=notes,
    )


# ----------------------------------------------------------- reminders ---

def reminder_dates(due: date, offsets=REMINDER_OFFSETS) -> list[tuple[int, date]]:
    return [(o, due + timedelta(days=o)) for o in offsets]


def reminder_kind(offset: int) -> str:
    if offset < 0:
        return "before"
    if offset == 0:
        return "due_day"
    return "after"
