"""Daily automation: send reminders that have fallen due, then rescore
dropout risk. Run once a day by the scheduler (see render.yaml).

Safe to run more than once a day: reminders are marked sent and each
one re-checks the live balance before sending, and risk scoring only
opens a task when no open task exists for the student.
"""
from django.core.management.base import BaseCommand

from core import clock
from reminders import services as reminders
from risk import services as risk


class Command(BaseCommand):
    help = "Send due fee reminders and rescore dropout risk for today."

    def handle(self, *args, **opts):
        today = clock.today()
        sent = reminders.dispatch_due(today)
        scored = risk.score_all(today)
        self.stdout.write(f"{today}: reminders {sent}; risk {scored}")
