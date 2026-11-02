"""Scheduling, sending and cancelling fee reminders.

The rule the client asked for: reminders go out on their own and stop
the moment someone pays. Two safeguards make that true:
  1. fees.services.apply_payment cancels scheduled reminders in the same
     transaction that clears the balance;
  2. the dispatcher re-checks the live balance before every send.
"""
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.money import format_inr
from fees import rules
from reminders.models import OutboundMessage, Reminder

MONTHS_HI = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]


def _date(d, lang):
    return f"{d.day} {MONTHS_HI[d.month - 1]} {d.year}" if lang == "hi" else d.strftime("%-d %b %Y")


def schedule_for(instalment):
    for offset, send_on in rules.reminder_dates(instalment.due_date):
        Reminder.objects.get_or_create(instalment=instalment, offset_days=offset, defaults={"send_on": send_on})


def cancel_for(instalment, reason):
    return Reminder.objects.filter(instalment=instalment, status=Reminder.Status.SCHEDULED).update(
        status=Reminder.Status.CANCELLED, cancelled_reason=reason)


def pay_url(token):
    base = getattr(settings, "PUBLIC_BASE_URL", "")
    return f"{base}/pay/{token}/"


def reminder_text(reminder, link_token, today):
    inst = reminder.instalment
    student = inst.plan.student
    lang = student.parent.preferred_language
    amount = format_inr(inst.balance)
    first = student.name.split()[0]
    url = pay_url(link_token)
    kind = rules.reminder_kind(reminder.offset_days)
    if lang == "hi":
        if kind == "before":
            lead = f"{first} की फ़ीस किस्त {amount} की देय तिथि {_date(inst.due_date, 'hi')} है।"
        elif kind == "due_day":
            lead = f"{first} की फ़ीस किस्त {amount} आज देय है।"
        else:
            lead = f"{first} की फ़ीस {amount} {(today - inst.due_date).days} दिन से बकाया है।"
        return f"{lead} भुगतान करें: {url} भुगतान होते ही ये संदेश बंद हो जाएँगे।"
    if kind == "before":
        lead = f"{first}'s fee instalment of {amount} is due on {_date(inst.due_date, 'en')}."
    elif kind == "due_day":
        lead = f"{first}'s fee instalment of {amount} is due today."
    else:
        lead = f"{first}'s fee of {amount} is {(today - inst.due_date).days} days overdue."
    return f"{lead} Pay here: {url} These messages stop as soon as you pay."


def dispatch_due(today):
    """Send every reminder due on or before `today`. Returns counts."""
    from fees import services as fee_services
    from fees.models import Student

    counts = {"sent": 0, "cancelled": 0, "superseded": 0}
    due = (Reminder.objects.filter(status=Reminder.Status.SCHEDULED, send_on__lte=today)
           .select_related("instalment__plan__student__parent").order_by("instalment_id", "send_on"))
    by_instalment = {}
    for r in due:
        by_instalment.setdefault(r.instalment_id, []).append(r)

    for reminders in by_instalment.values():
        with transaction.atomic():
            inst = reminders[0].instalment
            inst.refresh_from_db()
            student = inst.plan.student
            if inst.cancelled or inst.balance <= 0 or student.status != Student.Status.ACTIVE:
                n = Reminder.objects.filter(pk__in=[r.pk for r in reminders]).update(
                    status=Reminder.Status.CANCELLED, cancelled_reason="No balance at send time")
                counts["cancelled"] += n
                continue
            # If the job missed a few days, send only the most recent message
            # rather than a burst of out-of-date ones.
            *older, latest = reminders
            for r in older:
                r.status = Reminder.Status.CANCELLED
                r.cancelled_reason = "Superseded by a later reminder"
                r.save(update_fields=["status", "cancelled_reason"])
                counts["superseded"] += 1
            link = fee_services.get_or_create_link(inst)
            OutboundMessage.objects.create(
                to_phone=student.parent.phone or "", language=student.parent.preferred_language,
                kind=OutboundMessage.Kind.REMINDER, body=reminder_text(latest, link.token, today), reminder=latest)
            latest.status = Reminder.Status.SENT
            latest.sent_at = timezone.now()
            latest.save(update_fields=["status", "sent_at"])
            counts["sent"] += 1
    return counts


def send_receipt_message(payment, receipt):
    parent = payment.student.parent
    if parent.preferred_language == "hi":
        body = f"{format_inr(payment.amount)} प्राप्त हुए। रसीद {receipt.number}. धन्यवाद।"
    else:
        body = f"Received {format_inr(payment.amount)}. Receipt {receipt.number}. Thank you."
    OutboundMessage.objects.create(to_phone=parent.phone or "", language=parent.preferred_language,
                                   kind=OutboundMessage.Kind.RECEIPT, body=body)
