"""Unit tests for the pure fee rules. These are the rules the client said
must 'follow our written policy, not someone's calculator'."""
from datetime import date
from decimal import Decimal as D

import pytest

from fees import rules
from fees.rules import OpenInstalment, RuleError


# ------------------------------------------------------------- plans

def test_split_even():
    dates = [date(2026, 6, 10), date(2026, 10, 10), date(2027, 1, 10), date(2027, 3, 10)]
    assert rules.split_into_instalments(D("120000"), dates) == [D("30000")] * 4


def test_split_remainder_goes_to_last_and_sums_exactly():
    dates = [date(2026, 6, 1), date(2026, 9, 1), date(2026, 12, 1)]
    parts = rules.split_into_instalments(D("100000"), dates)
    assert parts == [D("33333"), D("33333"), D("33334")]
    assert sum(parts) == D("100000")


def test_split_after_discount_keeps_total():
    parts = rules.split_into_instalments(D("120000") - D("12500"), [date(2026, 6, 1)] * 1 + [date(2026, 9, 1), date(2027, 1, 1)])
    assert sum(parts) == D("107500")


@pytest.mark.parametrize("fee", [D("0"), D("-5")])
def test_split_rejects_non_positive(fee):
    with pytest.raises(RuleError):
        rules.split_into_instalments(fee, [date(2026, 6, 1)])


def test_split_rejects_unordered_dates():
    with pytest.raises(RuleError):
        rules.split_into_instalments(D("1000"), [date(2026, 9, 1), date(2026, 6, 1)])


# ---------------------------------------------- instalment state (brief data)

TODAY = date(2026, 11, 2)


def test_brief_aarav_paid():
    s = rules.instalment_state(D("30000"), D("30000"), date(2026, 10, 10), TODAY)
    assert s.status == "paid" and s.days_overdue == 0


def test_brief_ishita_part_paid_23_days_overdue():
    s = rules.instalment_state(D("30000"), D("15000"), date(2026, 10, 10), TODAY)
    assert s.status == "part_paid_overdue"
    assert s.days_overdue == 23


def test_brief_mohit_due_in_13_days():
    s = rules.instalment_state(D("40000"), D("0"), date(2026, 11, 15), TODAY)
    assert s.status == "due_soon"
    assert s.days_until_due == 13


def test_due_today_is_not_overdue():
    s = rules.instalment_state(D("100"), D("0"), TODAY, TODAY)
    assert s.status == "due_soon" and s.days_overdue == 0


def test_one_day_late_is_overdue():
    s = rules.instalment_state(D("100"), D("0"), date(2026, 11, 1), TODAY)
    assert s.status == "overdue" and s.days_overdue == 1


def test_far_future_is_upcoming():
    s = rules.instalment_state(D("100"), D("0"), date(2027, 1, 10), TODAY)
    assert s.status == "upcoming"


@pytest.mark.parametrize("days,bucket", [(0, None), (1, "1–30"), (30, "1–30"), (31, "31–60"),
                                         (60, "31–60"), (61, "61–90"), (90, "61–90"), (91, "90+"), (400, "90+")])
def test_ageing_bucket_edges(days, bucket):
    assert rules.ageing_bucket(days) == bucket


# ------------------------------------------------------------ allocation

def _open(*items):
    return [OpenInstalment(k, d, D(b)) for k, d, b in items]


def test_allocation_pays_oldest_first():
    items = _open((2, date(2026, 10, 10), "15000"), (3, date(2027, 1, 10), "30000"))
    allocs, left = rules.allocate_oldest_first(D("20000"), items)
    assert allocs == [(2, D("15000")), (3, D("5000"))]
    assert left == 0


def test_allocation_order_ignores_input_order():
    items = _open((3, date(2027, 1, 10), "30000"), (2, date(2026, 10, 10), "15000"))
    allocs, _ = rules.allocate_oldest_first(D("1000"), items)
    assert allocs == [(2, D("1000"))]


def test_allocation_reports_overpayment_instead_of_absorbing_it():
    items = _open((1, date(2026, 10, 10), "500"))
    allocs, left = rules.allocate_oldest_first(D("800"), items)
    assert allocs == [(1, D("500"))]
    assert left == D("300")


def test_allocation_skips_zero_balances():
    items = _open((1, date(2026, 6, 10), "0"), (2, date(2026, 10, 10), "100"))
    allocs, _ = rules.allocate_oldest_first(D("100"), items)
    assert allocs == [(2, D("100"))]


@pytest.mark.parametrize("amt", [D("0"), D("-1")])
def test_allocation_rejects_non_positive(amt):
    with pytest.raises(RuleError):
        rules.allocate_oldest_first(amt, [])


# ------------------------------------------------------------------- GST

def test_gst_split_matches_mockup_receipt():
    g = rules.gst_split(D("15000"))
    assert g.taxable == D("12711.86")
    assert g.cgst == D("1144.07") and g.sgst == D("1144.07")
    assert g.taxable + g.cgst + g.sgst == D("15000.00")


@pytest.mark.parametrize("amount", ["1", "999.99", "30000", "40000", "12345.67", "0.01"])
def test_gst_parts_always_sum_to_total(amount):
    g = rules.gst_split(D(amount))
    assert g.taxable + g.cgst + g.sgst == g.total


@pytest.mark.parametrize("d,fy", [(date(2026, 11, 2), "2026-27"), (date(2027, 3, 31), "2026-27"),
                                  (date(2027, 4, 1), "2027-28"), (date(2026, 1, 5), "2025-26")])
def test_financial_year(d, fy):
    assert rules.financial_year(d) == fy


# ---------------------------------------------------------------- refund

COURSE = dict(course_start=date(2026, 6, 1), course_end=date(2027, 5, 31))


def test_refund_worked_example_from_mockup():
    r = rules.calculate_refund(net_fee=D("120000"), amount_paid=D("60000"), last_day=date(2026, 11, 2),
                               request_date=date(2026, 11, 2), **COURSE)
    assert r.course_days == 365
    assert r.days_used == 155
    assert r.fee_for_days_used == D("50959")
    assert r.refund_due == D("9041")
    assert r.still_owed == 0
    assert r.pay_by == date(2026, 11, 12)


def test_refund_never_negative_when_underpaid():
    r = rules.calculate_refund(net_fee=D("120000"), amount_paid=D("30000"), last_day=date(2026, 11, 2),
                               request_date=date(2026, 11, 2), **COURSE)
    assert r.refund_due == 0
    assert r.still_owed == D("20959")
    assert any("nothing to refund" in n for n in r.notes)


def test_refund_before_course_start_returns_all_but_non_refundable():
    r = rules.calculate_refund(net_fee=D("120000"), amount_paid=D("30000"), last_day=date(2026, 5, 20),
                               request_date=date(2026, 5, 25), non_refundable=D("2000"), **COURSE)
    assert r.days_used == 0
    assert r.refund_due == D("28000")


def test_refund_after_course_end_is_zero():
    r = rules.calculate_refund(net_fee=D("120000"), amount_paid=D("120000"), last_day=date(2027, 6, 2),
                               request_date=date(2027, 6, 2), **COURSE)
    assert r.days_used == 365
    assert r.refund_due == 0


def test_refund_first_day_counts_as_used():
    r = rules.calculate_refund(net_fee=D("36500"), amount_paid=D("36500"), last_day=date(2026, 6, 1),
                               request_date=date(2026, 6, 1), **COURSE)
    assert r.days_used == 1
    assert r.fee_for_days_used == D("100")
    assert r.refund_due == D("36400")


def test_refund_non_refundable_cannot_exceed_net_fee():
    r = rules.calculate_refund(net_fee=D("1000"), amount_paid=D("1000"), last_day=date(2027, 5, 30),
                               request_date=date(2027, 5, 30), non_refundable=D("5000"), **COURSE)
    assert r.amount_retained == D("1000")
    assert r.refund_due == 0


def test_refund_uses_discounted_fee():
    full = rules.calculate_refund(net_fee=D("120000"), amount_paid=D("60000"), last_day=date(2026, 11, 2),
                                  request_date=date(2026, 11, 2), **COURSE)
    disc = rules.calculate_refund(net_fee=D("108000"), amount_paid=D("60000"), last_day=date(2026, 11, 2),
                                  request_date=date(2026, 11, 2), **COURSE)
    assert disc.refund_due > full.refund_due


def test_refund_rounding_half_up():
    # 1000 * 1 / 2 = 500.0 ; 1001 * 1 / 2 = 500.5 -> 501
    r = rules.calculate_refund(net_fee=D("1001"), amount_paid=D("1001"), course_start=date(2026, 1, 1),
                               course_end=date(2026, 1, 2), last_day=date(2026, 1, 1), request_date=date(2026, 1, 1))
    assert r.fee_for_days_used == D("501")
    assert r.refund_due == D("500")


def test_refund_rejects_last_day_after_request():
    with pytest.raises(RuleError):
        rules.calculate_refund(net_fee=D("1"), amount_paid=D("0"), last_day=date(2026, 11, 5),
                               request_date=date(2026, 11, 2), **COURSE)


def test_refund_rejects_inverted_course():
    with pytest.raises(RuleError):
        rules.calculate_refund(net_fee=D("1"), amount_paid=D("0"), course_start=date(2027, 1, 1),
                               course_end=date(2026, 1, 1), last_day=date(2026, 1, 1), request_date=date(2026, 1, 1))


# ------------------------------------------------------------- reminders

def test_reminder_dates_for_ishita():
    got = rules.reminder_dates(date(2026, 10, 10))
    assert got == [(-3, date(2026, 10, 7)), (0, date(2026, 10, 10)), (7, date(2026, 10, 17)),
                   (14, date(2026, 10, 24)), (30, date(2026, 11, 9))]
