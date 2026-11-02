"""Web-layer checks: sign-in, consent, who can see what, and paying by link."""
import re
from datetime import date
from decimal import Decimal as D

import pytest
from django.test import Client

from accounts.models import ConsentRecord, User
from core import clock
from fees import services
from fees.models import Instalment, Payment, PaymentLink
from reminders.models import Reminder
from tests import factories as f

pytestmark = pytest.mark.django_db
ON = date(2026, 11, 2)


@pytest.fixture(autouse=True)
def demo_today():
    clock.set_today(ON)


def parent_client(student, consent=True):
    c = Client()
    c.post("/parent/login/", {"phone": student.parent.phone})
    code = c.session["demo_otp"]
    r = c.post("/parent/verify/", {"code": code})
    assert r.status_code == 302
    if consent:
        c.post("/parent/consent/", {"agree": "yes"})
    return c


def staff_client(user):
    c = Client()
    assert c.login(username=user.username, password="pw-test-123!")
    return c


# ------------------------------------------------------------ parent sign-in

def test_wrong_code_is_rejected_and_does_not_sign_in():
    s = f.student()
    c = Client()
    c.post("/parent/login/", {"phone": s.parent.phone})
    r = c.post("/parent/verify/", {"code": "000000" if c.session["demo_otp"] != "000000" else "111111"})
    assert r.status_code == 200
    assert c.get("/parent/").status_code == 302


def test_unknown_number_gets_same_screen_as_known_one():
    # The login form must not reveal who is registered.
    c = Client()
    r = c.post("/parent/login/", {"phone": "9123456789"})
    assert r.status_code == 302 and r["Location"].endswith("/parent/verify/")


def test_invalid_phone_shows_error():
    r = Client().post("/parent/login/", {"phone": "12345"})
    assert r.status_code == 200 and "10" in r.content.decode()


def test_minor_needs_parental_consent_before_any_data():
    s = f.student(is_minor=True)
    c = parent_client(s, consent=False)
    assert c.get("/parent/")["Location"].endswith("/parent/consent/")
    assert c.get("/parent/progress/")["Location"].endswith("/parent/consent/")
    c.post("/parent/consent/", {})  # unticked box
    assert not ConsentRecord.objects.exists()
    c.post("/parent/consent/", {"agree": "yes"})
    assert c.get("/parent/").status_code == 200


def test_dues_screen_shows_amount_date_and_pay_button_in_hindi():
    s = f.student()
    services.apply_payment(student=s, amount=D("45000"), method="upi", source=Payment.Source.OFFLINE,
                           received_on=date(2026, 10, 18))
    html = parent_client(s).get("/parent/").content.decode()
    assert "₹15,000" in html and "10 Oct 2026" in html and "23 दिन देर" in html
    assert re.search(r'href="/pay/[\w-]+/"', html)


def test_language_toggle_switches_to_english_and_is_remembered():
    s = f.student()
    c = parent_client(s)
    c.post("/parent/language/", {"lang": "en", "next": "/parent/"})
    s.parent.refresh_from_db()
    assert s.parent.preferred_language == "en"
    assert "Amount due" in c.get("/parent/").content.decode()


def test_parent_cannot_open_another_familys_receipt():
    mine, theirs = f.student(), f.student(name="Aarav Meena")
    p = services.apply_payment(student=theirs, amount=D("30000"), method="upi", source=Payment.Source.OFFLINE,
                               received_on=ON)
    c = parent_client(mine)
    assert c.get(f"/parent/receipts/{p.receipt.number}.pdf").status_code == 404


def test_open_redirect_is_blocked_on_language_switch():
    r = Client().post("/parent/language/", {"lang": "en", "next": "//evil.example/"})
    assert "evil" not in r["Location"]


# -------------------------------------------------------------- pay by link

def test_paying_through_link_clears_balance_issues_receipt_and_stops_reminders():
    s = f.student()
    c = parent_client(s)
    token = re.search(r"/pay/([\w-]+)/", c.get("/parent/").content.decode()).group(1)
    link = PaymentLink.objects.get(token=token)
    r = c.post(f"/pay/{token}/", {"simulate": "success", "method": "upi"})
    done = c.get(r["Location"]).content.decode()
    inst = Instalment.objects.get(pk=link.instalment_id)
    assert inst.balance == 0
    assert "CGST" in done
    assert not Reminder.objects.filter(instalment=inst, status=Reminder.Status.SCHEDULED).exists()


def test_failed_payment_changes_nothing():
    s = f.student()
    c = parent_client(s)
    token = re.search(r"/pay/([\w-]+)/", c.get("/parent/").content.decode()).group(1)
    c.post(f"/pay/{token}/", {"simulate": "fail"})
    assert not Payment.objects.filter(student=s).exists()


def test_amount_that_does_not_match_goes_to_queue_not_ledger():
    s = f.student()
    c = parent_client(s)
    token = re.search(r"/pay/([\w-]+)/", c.get("/parent/").content.decode()).group(1)
    c.post(f"/pay/{token}/", {"simulate": "different", "amount": "12500"})
    assert not Payment.objects.filter(student=s).exists()
    from fees.models import UnmatchedPayment
    assert UnmatchedPayment.objects.filter(amount=D("12500")).exists()


def test_webhook_rejects_unsigned_calls():
    r = Client().post("/webhooks/gateway/", data=b'{"event_id":"x"}', content_type="application/json")
    assert r.status_code == 401


# ------------------------------------------------------------ staff access

def test_accounts_staff_only_see_their_centre():
    home = f.student()
    away = f.student(b=f.batch(f.centre("JPR1")))
    c = staff_client(f.staff(c=home.batch.centre))
    assert c.get(f"/office/students/{home.student_id}/").status_code == 200
    assert c.get(f"/office/students/{away.student_id}/").status_code == 404


def test_counsellor_cannot_reach_fee_screens():
    s = f.student()
    c = staff_client(f.staff(role=User.Role.COUNSELLOR, c=s.batch.centre))
    assert c.get("/office/tasks/").status_code == 200
    for url in ["/office/students/", f"/office/students/{s.student_id}/", "/office/refunds/", "/office/unmatched/"]:
        assert c.get(url).status_code == 403


def test_parent_account_cannot_use_office():
    s = f.student()
    c = parent_client(s)
    assert c.get("/office/").status_code in (302, 403)


def test_staff_pages_need_sign_in():
    assert Client().get("/office/students/")["Location"].startswith("/office/login/")
