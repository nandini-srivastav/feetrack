"""Turn a student's fee, attendance and test history into model inputs.

Each feature is defined so that a bigger number means more risk, which
keeps the explanation on screen honest ("+0.24 because attendance fell").
"""
from dataclasses import dataclass

from fees.models import Instalment

FEATURES = ["overdue_months", "missed_instalments", "attendance_gap", "attendance_drop", "score_drop"]

LABELS = {
    "overdue_months": "Oldest unpaid instalment is {days} days overdue",
    "missed_instalments": "{n} instalment(s) overdue",
    "attendance_gap": "Attendance over the last 2 weeks is {pct}%",
    "attendance_drop": "Attendance fell {before}% → {after}% over 4 weeks",
    "score_drop": "Last 3 tests: {scores}",
}


@dataclass
class FeatureRow:
    values: dict
    context: dict
    missing: list

    def vector(self):
        return [self.values[k] for k in FEATURES]


def build(student, today) -> FeatureRow:
    missing = []
    ctx = {}

    insts = [i for i in Instalment.objects.filter(plan__student=student, cancelled=False) if i.balance > 0]
    overdue = [i for i in insts if i.due_date < today]
    days = max(((today - i.due_date).days for i in overdue), default=0)
    ctx["days"] = days
    ctx["n"] = len(overdue)

    weeks = list(student.attendance.filter(week_start__lte=today).order_by("-week_start")[:4])
    weeks.reverse()
    if len(weeks) >= 2:
        pct = [w.classes_attended / w.classes_held if w.classes_held else 0 for w in weeks]
        recent = sum(pct[-2:]) / 2
        earlier = sum(pct[:-2]) / len(pct[:-2]) if len(pct) > 2 else recent
        gap, drop = 1 - recent, max(0.0, earlier - recent)
        ctx.update(pct=round(recent * 100), before=round(pct[0] * 100), after=round(pct[-1] * 100))
    else:
        gap = drop = None
        missing.append("attendance")

    tests = list(student.scores.filter(test_date__lte=today).order_by("-test_date")[:3])
    tests.reverse()
    if len(tests) >= 2:
        pcts = [t.percent / 100 for t in tests]
        prior = sum(pcts[:-1]) / len(pcts[:-1])
        score_drop = max(0.0, prior - pcts[-1])
        ctx["scores"] = " → ".join(f"{t.score:.0f}" for t in tests)
    else:
        score_drop = None
        missing.append("tests")

    values = {
        "overdue_months": min(days, 120) / 30,
        "missed_instalments": float(len(overdue)),
        "attendance_gap": gap,
        "attendance_drop": drop,
        "score_drop": score_drop,
    }
    return FeatureRow(values=values, context=ctx, missing=missing)
