"""Load realistic sample data, starting from the three students in the brief.

The simulation walks day by day from course start to the demo date,
applying payments on the days they happen and running the reminder job
each day, so reminder history, receipts and ageing are all produced by
the real code paths rather than typed in.

Usage:  python manage.py seed_demo [--reset | --if-empty]
Demo staff password comes from DEMO_PASSWORD (or is generated and printed).
"""
import os
import random
import secrets
from datetime import date, timedelta
from decimal import Decimal as D

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import TestScore, WeeklyAttendance
from accounts.models import User
from core import clock
from fees import services
from fees.models import Batch, Centre, Instalment, Payment, RefundPolicy, Student
from reminders import services as reminders
from risk import services as risk

DEMO_TODAY = date(2026, 11, 2)
COURSE_START, COURSE_END = date(2026, 6, 1), date(2027, 5, 31)
JEE_DATES = [date(2026, 6, 10), date(2026, 10, 10), date(2027, 1, 10), date(2027, 3, 10)]
NEET_DATES = [date(2026, 6, 15), date(2026, 11, 15), date(2027, 2, 15)]
TESTS = [("Mock test 4 · Chemistry", date(2026, 9, 28)), ("Mock test 5 · Physics + Maths", date(2026, 10, 12)),
         ("Mock test 6 · Full syllabus", date(2026, 10, 26))]

FIRST = ["Aditya", "Ananya", "Arjun", "Diya", "Harsh", "Isha", "Kabir", "Kavya", "Krish", "Meera", "Neha", "Nikhil",
         "Pooja", "Pranav", "Riya", "Rohan", "Saanvi", "Sahil", "Shreya", "Tanvi", "Varun", "Yash", "Zoya", "Aman",
         "Bhavya", "Devansh", "Garima", "Jatin", "Khushi", "Lakshya", "Manav", "Nandini", "Ojas", "Palak", "Raghav",
         "Sneha", "Tushar", "Vidhi"]
LAST = ["Agarwal", "Bairwa", "Choudhary", "Gupta", "Jain", "Joshi", "Khandelwal", "Kumawat", "Mathur", "Meena",
        "Rathore", "Saini", "Sharma", "Shekhawat", "Singh", "Soni", "Vyas", "Yadav"]


def mondays_before(day, n):
    last = day - timedelta(days=day.weekday() or 7)
    return [last - timedelta(weeks=i) for i in reversed(range(n))]


class Command(BaseCommand):
    help = "Seed demo data (3 students from the brief + a generated population)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing data first")
        parser.add_argument("--students", type=int, default=160)
        parser.add_argument("--if-empty", action="store_true",
                            help="Seed only when the database has no students (safe on every deploy)")

    def handle(self, *args, reset, students, if_empty=False, **opts):
        if if_empty and Student.objects.exists():
            self.stdout.write("Data already present; seed skipped.")
            return
        if Student.objects.exists() and not reset:
            raise CommandError("Data already exists. Use --reset to replace it.")
        self.rng = random.Random(2026)
        with transaction.atomic():
            if reset:
                self._wipe()
            password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(9)
            self._base(password)
            self._brief_students()
            self._population(students)
            self._simulate()
            self._unmatched()
            clock.set_today(DEMO_TODAY)
            counts = risk.score_all(DEMO_TODAY)
        self.stdout.write(self.style.SUCCESS(f"Seeded {Student.objects.count()} students. Risk: {counts}"))
        if not os.environ.get("DEMO_PASSWORD"):
            self.stdout.write(f"Staff password (all demo staff): {password}")

    # ------------------------------------------------------------------

    def _wipe(self):
        from django.apps import apps

        for label in ["risk.CounsellorTask", "risk.RiskAssessment", "reminders.OutboundMessage", "reminders.Reminder",
                      "fees.Refund", "fees.UnmatchedPayment", "fees.GatewayEvent", "fees.PaymentLink", "fees.Receipt",
                      "fees.Allocation", "fees.Payment", "fees.ReceiptCounter", "fees.Instalment", "fees.FeePlan",
                      "accounts.ConsentRecord", "academics.WeeklyAttendance", "academics.TestScore", "fees.Student",
                      "fees.Batch", "accounts.OTPChallenge", "core.AuditLog"]:
            apps.get_model(label).objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        Centre.objects.all().delete()
        RefundPolicy.objects.all().delete()

    def _base(self, password):
        RefundPolicy.objects.create(
            version="v1.0", published_on=date(2026, 4, 1), is_active=True, non_refundable_amount=D("0"), payout_days=10,
            summary=("If a student leaves, we keep the fee for the days the course ran up to and including the last "
                     "day attended, counted from course start, and refund the rest of what was paid within 10 days."))
        self.centres = [Centre.objects.create(code=f"JPR{i}", name=f"Centre {i}", gstin="[INSTITUTE GSTIN]")
                        for i in range(1, 5)]
        self.batches = {}
        for c in self.centres:
            for name in ["JEE 2027 Morning", "JEE 2027 Evening", "NEET 2027 Morning", "NEET 2027 Evening"]:
                self.batches[(c.code, name)] = Batch.objects.create(centre=c, name=name, course_start=COURSE_START,
                                                                    course_end=COURSE_END)
        c2 = self.centres[1]
        for username, first, last, role, centre in [
            ("accounts", "Priya", "Joshi", User.Role.ACCOUNTS, c2),
            ("accounts.c1", "Rahul", "Verma", User.Role.ACCOUNTS, self.centres[0]),
            ("counsellor", "Anita", "Rao", User.Role.COUNSELLOR, c2),
            ("director", "S.", "Khandelwal", User.Role.DIRECTOR, None),
        ]:
            u = User(username=username, first_name=first, last_name=last, role=role, centre=centre)
            u.set_password(password)
            u.save()
        self.plans = []  # (student, behaviour, attendance pattern, scores)

    def _parent(self, student_id, surname, phone=None, lang="hi"):
        n = int(student_id.split("-")[1])
        return User.objects.create(username=f"parent-{student_id.lower()}", first_name="Parent of", last_name=surname,
                                   phone=phone or f"98765{n:05d}", preferred_language=lang)

    def _student(self, sid, name, batch, behaviour, att, scores, lang="hi", discount=D("0")):
        surname = name.split()[-1]
        s = Student.objects.create(student_id=sid, name=name, batch=batch, parent=self._parent(sid, surname, lang=lang))
        neet = batch.name.startswith("NEET")
        services.create_plan(s, D("120000"), NEET_DATES if neet else JEE_DATES, discount=discount,
                             discount_reason="Merit scholarship" if discount else "")
        self.plans.append((s, behaviour, att, scores))
        return s

    def _brief_students(self):
        b = lambda name: self.batches[("JPR2", name)]  # noqa: E731
        # From the brief's sample table, extended with plausible history.
        self._student("STU-0412", "Aarav Meena", b("JEE 2027 Morning"),
                      {1: [(date(2026, 6, 6), "30000")], 2: [(date(2026, 10, 8), "30000")]},
                      [95, 94, 96, 93, 97, 95, 96, 97], [176, 184, 191], lang="en")
        self._student("STU-0587", "Ishita Sharma", b("JEE 2027 Evening"),
                      {1: [(date(2026, 6, 6), "30000")], 2: [(date(2026, 10, 18), "15000")]},
                      [94, 93, 95, 92, 92, 86, 78, 68], [142, 131, 118])
        self._student("STU-0219", "Mohit Gurjar", b("NEET 2027 Morning"),
                      {1: [(date(2026, 6, 12), "40000")]},
                      [90, 88, 91, 89, 92, 90, 87, 90], [402, 418, 431])
        # Used by the unmatched-payments demo: part paid, then sent the rest by plain UPI.
        self._student("STU-0733", "Kavya Agarwal", b("JEE 2027 Evening"),
                      {1: [(date(2026, 6, 8), "30000")], 2: [(date(2026, 10, 9), "15000")]},
                      [90, 91, 89, 92, 90, 88, 91, 90], [150, 156, 149])

    def _population(self, n):
        rng = self.rng
        used = {"STU-0412", "STU-0587", "STU-0219", "STU-0733"}
        keys = list(self.batches)
        for _ in range(n):
            while True:
                sid = f"STU-{rng.randint(100, 2999):04d}"
                if sid not in used:
                    used.add(sid)
                    break
            batch = self.batches[rng.choice(keys)]
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            kind = rng.choices(["on_time", "late", "partial", "stopped"], [64, 20, 6, 10])[0]
            neet = batch.name.startswith("NEET")
            dates = NEET_DATES if neet else JEE_DATES
            discount = D("12000") if rng.random() < 0.1 else D("0")
            amounts = services.rules.split_into_instalments(D("120000") - discount, dates)
            pays = {}
            for i, (due, amt) in enumerate(zip(dates, amounts), start=1):
                if due > DEMO_TODAY:
                    if kind == "on_time" and rng.random() < 0.15 and due - DEMO_TODAY < timedelta(days=20):
                        pays[i] = [(DEMO_TODAY - timedelta(days=rng.randint(0, 3)), str(amt))]
                    continue
                if kind == "on_time":
                    pays[i] = [(due - timedelta(days=rng.randint(0, 6)), str(amt))]
                elif kind == "late":
                    pays[i] = [(due + timedelta(days=rng.randint(4, 70)), str(amt))]
                elif kind == "partial":
                    half = (amt / 2).quantize(D("1"))
                    pays[i] = [(due + timedelta(days=rng.randint(0, 20)), str(half))]
                elif kind == "stopped" and i == 1:
                    pays[i] = [(due + timedelta(days=rng.randint(0, 15)), str(amt))]
            if kind == "stopped":
                base = rng.uniform(80, 92)
                att = [base] * 4 + [base - 6, base - 12, base - 20, base - 28]
                s0 = rng.uniform(110, 170)
                scores = [s0, s0 - rng.uniform(5, 20), s0 - rng.uniform(15, 35)]
            else:
                base = rng.uniform(78, 98)
                att = [min(100, base + rng.uniform(-6, 4)) for _ in range(8)]
                s0 = rng.uniform(100, 230)
                scores = [s0 + rng.uniform(-15, 20) for _ in range(3)]
            self._student(sid, name, batch, pays, [round(max(20, a)) for a in att],
                          [round(max(20, sc)) for sc in scores], lang="hi" if rng.random() < 0.7 else "en",
                          discount=discount)

    def _simulate(self):
        """Walk the calendar: payments land on their day, reminder job runs daily."""
        rng = self.rng
        by_day = {}
        for s, pays, att, scores in self.plans:
            for inst_no, items in pays.items():
                for day, amount in items:
                    by_day.setdefault(min(day, DEMO_TODAY), []).append((s, inst_no, D(amount)))
            neet = s.batch.name.startswith("NEET")
            max_score = D(720) if neet else D(300)
            for week, pct in zip(mondays_before(DEMO_TODAY, 8), att):
                WeeklyAttendance.objects.create(student=s, week_start=week, classes_held=24,
                                                classes_attended=round(24 * min(pct, 100) / 100))
            for (test_name, day), sc in zip(TESTS, scores):
                TestScore.objects.create(student=s, test_name=test_name, test_date=day,
                                         score=min(D(sc), max_score), max_score=max_score)

        day = COURSE_START - timedelta(days=10)
        while day <= DEMO_TODAY:
            for s, inst_no, amount in by_day.get(day, []):
                inst = Instalment.objects.get(plan__student=s, number=inst_no)
                if inst.balance <= 0:
                    continue
                method = rng.choice(["upi", "upi", "upi", "card", "netbanking", "cash"])
                source = Payment.Source.OFFLINE if method == "cash" else Payment.Source.GATEWAY
                services.apply_payment(student=s, amount=min(amount, inst.balance), method=method, source=source,
                                       received_on=day, reference=f"SIM{rng.randint(10**7, 10**8)}",
                                       instalment=inst)
            reminders.dispatch_due(day)
            day += timedelta(days=1)

    def _unmatched(self):
        c2 = self.centres[1]
        b = self.batches[("JPR2", "JEE 2027 Evening")]
        from fees.models import UnmatchedPayment

        kavya = Student.objects.get(student_id="STU-0733")

        u = UnmatchedPayment.objects.create(amount=D("15000"), method="upi", reference="UPI/431109877",
                                            note="KAVYA FEE", received_on=date(2026, 10, 31), centre=c2,
                                            reason="Direct UPI transfer, not through a payment link")
        s, why = services.suggest_match(u)
        if s:
            u.suggested_student, u.suggestion_reason = s, why
            u.save()
        UnmatchedPayment.objects.create(amount=D("30000"), method="bank", reference="NEFT N30447120", note="",
                                        received_on=date(2026, 10, 30), centre=c2,
                                        reason="Bank transfer with no reference")
        # A parent paid a different amount through a link: the real webhook path routes it to the queue.
        target = (Instalment.objects.filter(plan__student__batch__centre=c2, cancelled=False, due_date__lt=DEMO_TODAY)
                  .exclude(plan__student=kavya).filter(paid_amount=0).first())
        if target:
            link = services.get_or_create_link(target)
            services.process_gateway_event({"event_id": "evt_demo_mismatch", "status": "captured",
                                            "link_token": link.token, "amount": "12500", "paid_on": "2026-10-29",
                                            "method": "upi", "reference": "UPI/429918231"})
