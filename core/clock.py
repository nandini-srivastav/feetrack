"""Single source of 'today' for all business rules (see DemoClock)."""
from django.utils import timezone


def today():
    from core.models import DemoClock

    clock = DemoClock.objects.filter(pk=1).only("simulated_date").first()
    if clock and clock.simulated_date:
        return clock.simulated_date
    return timezone.localdate()


def set_today(value):
    from core.models import DemoClock

    DemoClock.objects.update_or_create(pk=1, defaults={"simulated_date": value})
