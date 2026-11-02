from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from parents import views as parent_views

urlpatterns = [
    path("", TemplateView.as_view(template_name="landing.html"), name="landing"),
    path("parent/", include("parents.urls")),
    path("office/", include("office.urls")),
    path("pay/<str:token>/", parent_views.pay, name="pay"),
    path("pay/<str:token>/done/", parent_views.pay_done, name="pay_done"),
    path("webhooks/gateway/", parent_views.gateway_webhook, name="gateway_webhook"),
    path("django-admin/", admin.site.urls),
]

handler403 = "core.views.forbidden"
handler404 = "core.views.not_found"
