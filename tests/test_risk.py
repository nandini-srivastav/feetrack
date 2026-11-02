from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone

from academics.models import TestScore, WeeklyAttendance
from fees.models import Payment
from reminders.models import OutboundMessage
from risk import features, model, services
from risk.models import CounsellorTask
from tests import factories as f

pytestmark = pytest.mark.django_db
TODAY = date(2026, 11, 2)


def history(s, weeks, scores):
    start = TODAY - timedelta(days=7 * len(weeks))
    for i, pct in enumerate(weeks):
        WeeklyAttendance.objects.create(student=s, week_start=start + timedelta(days=7 * i),
                                        classes_held=25, classes_attended=round(25 * pct / 100))
    for i, sc in enumerate(scores):
        TestScore.objects.create(student=s, test_name=f"Mock {i}", test_date=TODAY - timedelta(days=14 * (len(scores) - i)),
                                 score=D(sc), max_score=D(300))


def ishita():
    """The brief's at-risk shape: part paid, 23 days overdue, slipping."""
    s = f.student()
    from fees import services as fs
    fs.apply_payment(student=s, amount=D("45000"), method="upi", source="offline", received_on=date(2026, 10, 18))
    history(s, [92, 86, 78, 68], [142, 131, 118])
    return s


def steady():
    s = f.student(name="Aarav Meena")
    from fees import services as fs
    fs.apply_payment(student=s, amount=D("60000"), method="upi", source="offline", received_on=date(2026, 10, 5))
    history(s, [94, 96, 95, 97], [180, 186, 191])
    return s


def test_features_describe_the_student():
    row = features.build(ishita(), TODAY)
    assert row.context["days"] == 23
    assert row.values["missed_instalments"] == 1
    assert row.context["before"] == 92 and row.context["after"] == 68
    assert row.context["scores"] == "142 → 131 → 118"
    assert row.missing == []


def test_struggling_student_is_flagged_and_steady_one_is_not():
    a, decision_a = services.assess(ishita(), TODAY)
    b, decision_b = services.assess(steady(), TODAY)
    assert decision_a == "flag" and decision_b == "below_threshold"
    assert a.score > b.score


def test_explanation_lists_reasons_with_biggest_first():
    a, _ = services.assess(ishita(), TODAY)
    impacts = [c["impact"] for c in a.contributions]
    assert impacts == sorted(impacts, reverse=True)
    assert a.contributions[0]["label"]


def test_flag_never_touches_fees_or_messages_parents():
    s = ishita()
    payments, messages = Payment.objects.count(), OutboundMessage.objects.count()
    services.assess(s, TODAY)
    assert Payment.objects.count() == payments
    assert OutboundMessage.objects.count() == messages
    assert CounsellorTask.objects.filter(student=s).count() == 1


def test_no_duplicate_open_task():
    s = ishita()
    services.assess(s, TODAY)
    _, decision = services.assess(s, TODAY)
    assert decision == "already_open"
    assert CounsellorTask.objects.filter(student=s).count() == 1


def test_student_with_no_attendance_or_tests_is_not_flagged_even_if_overdue():
    s = f.student()
    _, decision = services.assess(s, date(2027, 2, 20))  # two instalments well overdue
    assert decision in {"insufficient_data", "below_threshold"}
    assert not CounsellorTask.objects.filter(student=s).exists()


def test_false_flag_quiets_student_for_30_days():
    s = ishita()
    services.assess(s, TODAY)
    task = CounsellorTask.objects.get(student=s)
    services.close_task(task, user=f.staff(), false_flag=True)
    _, decision = services.assess(s, timezone.localdate())
    assert decision == "quiet_after_false_flag"


def test_closing_twice_is_rejected():
    s = ishita()
    services.assess(s, TODAY)
    task = CounsellorTask.objects.get(student=s)
    user = f.staff()
    services.close_task(task, user=user, false_flag=False, outcome="Reached parent")
    with pytest.raises(ValueError):
        services.close_task(task, user=user, false_flag=True)


def test_missing_inputs_are_neutral():
    row = features.FeatureRow(values={k: None for k in features.FEATURES}, context={}, missing=["attendance", "tests"])
    p, contribs = model.score(row)
    assert all(c["impact"] == 0 for c in contribs)
    assert 0 < p < 1
