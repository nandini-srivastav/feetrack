from datetime import date
from decimal import Decimal as D

from accounts.models import User
from fees import services
from fees.models import Batch, Centre, RefundPolicy, Student

JEE_DATES = [date(2026, 6, 10), date(2026, 10, 10), date(2027, 1, 10), date(2027, 3, 10)]


def centre(code="JPR2"):
    return Centre.objects.get_or_create(code=code, defaults={"name": f"Centre {code}"})[0]


def batch(c=None, name="JEE 2027 Evening"):
    return Batch.objects.get_or_create(centre=c or centre(), name=name, defaults={
        "course_start": date(2026, 6, 1), "course_end": date(2027, 5, 31)})[0]


_n = [0]


def student(b=None, name="Ishita Sharma", fee=D("120000"), dates=None, lang="hi", **kw):
    _n[0] += 1
    parent = User.objects.create(username=f"parent{_n[0]}", phone=f"90000{_n[0]:05d}", preferred_language=lang)
    s = Student.objects.create(student_id=f"STU-{_n[0]:04d}", name=name, batch=b or batch(), parent=parent, **kw)
    services.create_plan(s, fee, dates or JEE_DATES)
    return s


def staff(role=User.Role.ACCOUNTS, c=None, username=None):
    _n[0] += 1
    u = User.objects.create_user(username=username or f"staff{_n[0]}", password="pw-test-123!", role=role,
                                 centre=c if role != User.Role.DIRECTOR else None)
    return u


def policy(non_refundable=D("0")):
    return RefundPolicy.objects.create(version="v1.0", published_on=date(2026, 4, 1), is_active=True,
                                       non_refundable_amount=non_refundable, summary="Pro-rata by days.")
