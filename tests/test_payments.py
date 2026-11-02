from datetime import date
from decimal import Decimal as D

import pytest

from fees import services
from fees.models import GatewayEvent, Instalment, Payment, PaymentLink, Receipt, UnmatchedPayment
from fees.services import PaymentError
from reminders.models import OutboundMessage, Reminder
from tests import factories as f

pytestmark = pytest.mark.django_db
ON = date(2026, 11, 2)


def inst(s, n):
    return Instalment.objects.get(plan__student=s, number=n)


def pay(s, amount, instalment=None):
    return services.apply_payment(student=s, amount=D(amount), method="upi", source=Payment.Source.OFFLINE,
                                  received_on=ON, instalment=instalment)


def test_plan_creates_instalments_and_five_reminders_each():
    s = f.student()
    assert [i.amount for i in Instalment.objects.filter(plan__student=s)] == [D("30000")] * 4
    assert Reminder.objects.filter(instalment__plan__student=s).count() == 20


def test_payment_pays_oldest_first_and_cancels_reminders_only_when_cleared():
    s = f.student()
    pay(s, "45000")
    i1, i2 = inst(s, 1), inst(s, 2)
    assert (i1.paid_amount, i2.paid_amount) == (D("30000"), D("15000"))
    assert not i1.reminders.filter(status="scheduled").exists()
    assert i2.reminders.filter(status="scheduled").exists()  # balance remains, reminders continue


def test_overpayment_is_rejected_and_nothing_is_written():
    s = f.student()
    with pytest.raises(PaymentError):
        pay(s, "120001")
    assert Payment.objects.count() == 0
    assert inst(s, 1).paid_amount == 0


def test_payment_to_a_specific_instalment_cannot_spill_over():
    s = f.student()
    with pytest.raises(PaymentError):
        pay(s, "30001", instalment=inst(s, 2))


def test_receipt_numbers_are_sequential_per_centre_and_year():
    s = f.student()
    p1, p2 = pay(s, "100"), pay(s, "200")
    assert p1.receipt.number == "JPR2-26-27-000001"
    assert p2.receipt.number == "JPR2-26-27-000002"
    assert p1.receipt.taxable_value + p1.receipt.cgst + p1.receipt.sgst == D("100.00")


def test_receipt_message_goes_out_in_parent_language():
    s = f.student(lang="hi")
    pay(s, "100")
    msg = OutboundMessage.objects.get(kind="receipt")
    assert "रसीद" in msg.body


# ----------------------------------------------------------- gateway

def event(link, **over):
    payload = {"event_id": "evt_1", "status": "captured", "link_token": link.token, "amount": str(link.amount),
               "paid_on": "2026-11-02", "method": "upi", "reference": "UPI123"}
    payload.update(over)
    return payload


def test_gateway_event_applies_payment_and_stops_reminders():
    s = f.student()
    pay(s, "45000")
    link = services.get_or_create_link(inst(s, 2))
    assert link.amount == D("15000")
    out = services.process_gateway_event(event(link))
    assert out.outcome == "applied"
    assert inst(s, 2).balance == 0
    assert not inst(s, 2).reminders.filter(status="scheduled").exists()
    link.refresh_from_db()
    assert link.status == PaymentLink.Status.USED


def test_gateway_retry_is_idempotent():
    s = f.student()
    link = services.get_or_create_link(inst(s, 1))
    services.process_gateway_event(event(link))
    again = services.process_gateway_event(event(link))
    assert again.outcome == "duplicate"
    assert Payment.objects.count() == 1


def test_gateway_amount_mismatch_goes_to_queue_not_paid():
    s = f.student()
    link = services.get_or_create_link(inst(s, 1))
    out = services.process_gateway_event(event(link, amount="12500"))
    assert out.outcome == "unmatched"
    assert inst(s, 1).paid_amount == 0
    u = UnmatchedPayment.objects.get()
    assert u.suggested_student == s and "differs" in u.reason


def test_gateway_used_link_goes_to_queue():
    s = f.student()
    link = services.get_or_create_link(inst(s, 1))
    services.process_gateway_event(event(link))
    out = services.process_gateway_event(event(link, event_id="evt_2"))
    assert out.outcome == "unmatched"
    assert Payment.objects.count() == 1


def test_gateway_unknown_link_goes_to_queue():
    out = services.process_gateway_event({"event_id": "e", "status": "captured", "link_token": "nope",
                                          "amount": "100", "paid_on": "2026-11-02"})
    assert out.outcome == "unmatched"


def test_gateway_failed_payment_changes_nothing():
    s = f.student()
    link = services.get_or_create_link(inst(s, 1))
    out = services.process_gateway_event(event(link, status="failed"))
    assert out.outcome == "failed"
    assert Payment.objects.count() == 0 and UnmatchedPayment.objects.count() == 0
    assert GatewayEvent.objects.get().outcome == "failed"


def test_part_payment_voids_old_link_and_new_link_has_new_amount():
    s = f.student()
    old = services.get_or_create_link(inst(s, 1))
    pay(s, "10000")
    old.refresh_from_db()
    assert old.status == PaymentLink.Status.VOID
    assert services.get_or_create_link(inst(s, 1)).amount == D("20000")


def test_no_link_for_paid_instalment():
    s = f.student()
    pay(s, "30000")
    with pytest.raises(PaymentError):
        services.get_or_create_link(inst(s, 1))


# ----------------------------------------------------------- unmatched

def test_resolve_unmatched_applies_once():
    s = f.student()
    u = UnmatchedPayment.objects.create(amount=D("30000"), method="bank", received_on=ON, reason="No reference")
    user = f.staff()
    services.resolve_unmatched(u, s, user)
    assert inst(s, 1).paid_amount == D("30000")
    with pytest.raises(PaymentError):
        services.resolve_unmatched(u, s, user)


def test_suggest_match_by_name_and_amount():
    s = f.student(name="Kavya Agarwal")
    u = UnmatchedPayment.objects.create(amount=D("30000"), method="upi", received_on=ON, reason="x",
                                        note="KAVYA FEE")
    found, why = services.suggest_match(u)
    assert found == s and "Kavya" in why


def test_suggest_match_needs_amount_to_line_up():
    f.student(name="Kavya Agarwal")
    u = UnmatchedPayment.objects.create(amount=D("777"), method="upi", received_on=ON, reason="x", note="kavya")
    assert services.suggest_match(u) == (None, "")


# ------------------------------------------------------------- refunds

def test_refund_approval_withdraws_and_stops_everything():
    f.policy()
    s = f.student(name="Aarav Meena")
    pay(s, "60000")
    refund = services.approve_refund(s, date(2026, 11, 2), date(2026, 11, 2), "Moving city", f.staff())
    assert refund.refund_due == D("9041")
    s.refresh_from_db()
    assert s.status == "withdrawn"
    assert inst(s, 3).cancelled and inst(s, 4).cancelled
    assert not Reminder.objects.filter(instalment__plan__student=s, status="scheduled").exists()
    with pytest.raises(PaymentError):
        pay(s, "100")
    with pytest.raises(PaymentError):
        services.approve_refund(s, date(2026, 11, 2), date(2026, 11, 2), "again", f.staff())


def test_refund_needs_a_published_policy():
    from fees.rules import RuleError

    s = f.student()
    with pytest.raises(RuleError):
        services.preview_refund(s, date(2026, 11, 2), date(2026, 11, 2))
