"""Read-only aggregates for the dues dashboard and the Tally export."""
import csv
from collections import defaultdict
from decimal import Decimal
from io import StringIO

from fees import rules
from fees.models import Instalment, Receipt

ZERO = Decimal("0")
BUCKETS = [b[2] for b in rules.AGEING_BUCKETS]


def _open_instalments(students_qs):
    return (Instalment.objects.filter(plan__student__in=students_qs, cancelled=False)
            .select_related("plan__student__batch__centre"))


def dues_overview(students_qs, today):
    """Everything the dashboard shows, from one pass over instalments."""
    by_centre = defaultdict(lambda: {b: ZERO for b in BUCKETS})
    by_batch = defaultdict(lambda: {"students": set(), "overdue": ZERO, "over60": ZERO, "billed": ZERO, "batch": None})
    overdue_students = set()
    billed = collected = overdue = over60 = ZERO
    due_next_14 = ZERO

    for inst in _open_instalments(students_qs):
        student = inst.plan.student
        centre, batch = student.batch.centre, student.batch
        if inst.due_date <= today:
            billed += inst.amount
            collected += inst.paid_amount
            by_batch[batch.pk]["billed"] += inst.amount
        state = inst.state(today)
        if state.is_overdue:
            bucket = rules.ageing_bucket(state.days_overdue)
            by_centre[centre][bucket] += inst.balance
            overdue += inst.balance
            overdue_students.add(student.pk)
            row = by_batch[batch.pk]
            row["batch"] = batch
            row["students"].add(student.pk)
            row["overdue"] += inst.balance
            if state.days_overdue > 60:
                over60 += inst.balance
                row["over60"] += inst.balance
        elif 0 <= (inst.due_date - today).days <= 14:
            due_next_14 += inst.balance

    centres = []
    max_total = max((sum(v.values()) for v in by_centre.values()), default=ZERO) or Decimal("1")
    for centre, buckets in sorted(by_centre.items(), key=lambda kv: kv[0].code):
        total = sum(buckets.values())
        centres.append({
            "centre": centre, "total": total,
            "segments": [{"label": b, "amount": buckets[b], "cls": f"age-{i + 1}",
                          "width": float(buckets[b] / max_total * 100)} for i, b in enumerate(BUCKETS)],
        })
    batches = sorted(
        [{**r, "students": len(r["students"])} for r in by_batch.values() if r["batch"]],
        key=lambda r: r["overdue"], reverse=True)

    return {
        "billed": billed, "collected": collected,
        "collection_rate": (collected / billed * 100) if billed else None,
        "overdue": overdue, "overdue_students": len(overdue_students),
        "over60": over60, "over60_share_of_billed": (over60 / billed * 100) if billed else None,
        "due_next_14": due_next_14,
        "centres": centres, "batches": batches,
    }


TALLY_HEADER = ["Date", "Voucher Type", "Voucher No", "Party Ledger", "Student ID", "Centre", "Mode",
                "Reference", "Taxable Value", "CGST", "SGST", "Total", "Narration"]


def tally_csv(receipts_qs) -> str:
    """CSV laid out one receipt voucher per row for Tally import.

    Assumption: the client's Tally has ledgers named after student IDs and
    imports receipts via a CSV/Excel template; column order is configurable
    there. A native Tally XML export is listed as future work.
    """
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(TALLY_HEADER)
    for r in receipts_qs.select_related("payment__student__batch__centre").order_by("payment__received_on", "number"):
        p = r.payment
        writer.writerow([p.received_on.strftime("%d-%m-%Y"), "Receipt", r.number, p.student.name, p.student.student_id,
                         p.student.batch.centre.code, p.get_method_display(), p.reference, r.taxable_value, r.cgst,
                         r.sgst, r.total, f"Fee received - {p.student.batch.name}"])
    return out.getvalue()


def receipts_between(students_qs, start, end):
    return Receipt.objects.filter(payment__student__in=students_qs, payment__received_on__range=(start, end))
