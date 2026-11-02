from datetime import date
from decimal import Decimal as D

import pytest

from fees import services
from fees.models import Instalment, Payment
from reminders import services as rs
from reminders.models import OutboundMessage, Reminder
from tests import factories as f

pytestmark = pytest.mark.django_db


def test_sends_due_reminder_in_hindi_with_pay_link():
    s = f.student(lang="hi")
    counts = rs.dispatch_due(date(2026, 6, 7))  # 3 days before instalment 1
    assert counts["sent"] == 1
    msg = OutboundMessage.objects.get(kind="reminder")
    assert "/pay/" in msg.body and "₹30,000" in msg.body and "देय" in msg.body


def test_english_parent_gets_english():
    f.student(lang="en")
    rs.dispatch_due(date(2026, 6, 10))
    assert "due today" in OutboundMessage.objects.get(kind="reminder").body


def test_payment_before_send_date_means_reminder_never_goes():
    """The client's rule: reminders stop the moment someone pays."""
    s = f.student()
    services.apply_payment(student=s, amount=D("30000"), method="upi", source=Payment.Source.OFFLINE,
                           received_on=date(2026, 6, 5))
    counts = rs.dispatch_due(date(2026, 7, 10))  # all of instalment 1's reminders are now due
    assert counts["sent"] == 0
    assert not OutboundMessage.objects.filter(kind="reminder").exists()


def test_dispatcher_rechecks_balance_even_if_cancel_was_missed():
    s = f.student()
    i1 = Instalment.objects.get(plan__student=s, number=1)
    Instalment.objects.filter(pk=i1.pk).update(paid_amount=D("30000"))  # bypass the service on purpose
    counts = rs.dispatch_due(date(2026, 6, 10))
    assert counts["sent"] == 0 and counts["cancelled"] == 2


def test_missed_days_send_only_the_latest_reminder():
    f.student()
    counts = rs.dispatch_due(date(2026, 6, 24))  # -3, 0, +7, +14 all due at once
    assert counts == {"sent": 1, "cancelled": 0, "superseded": 3}
    sent = Reminder.objects.get(status="sent")
    assert sent.offset_days == 14
    assert "14 दिन" in OutboundMessage.objects.get(kind="reminder").body


def test_running_twice_sends_nothing_new():
    f.student()
    rs.dispatch_due(date(2026, 6, 10))
    assert rs.dispatch_due(date(2026, 6, 10))["sent"] == 0
