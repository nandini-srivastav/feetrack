from django.conf import settings

from core import clock


def app_context(request):
    return {"DEMO_MODE": settings.DEMO_MODE, "TODAY": clock.today()}
