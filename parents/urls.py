from django.urls import path

from parents import views

app_name = "parents"
urlpatterns = [
    path("login/", views.login_phone, name="login"),
    path("verify/", views.verify_code, name="verify"),
    path("consent/", views.consent, name="consent"),
    path("logout/", views.sign_out, name="logout"),
    path("language/", views.set_language, name="language"),
    path("", views.home, name="home"),
    path("receipts/", views.receipts, name="receipts"),
    path("receipts/<str:number>.pdf", views.receipt_pdf, name="receipt_pdf"),
    path("progress/", views.progress, name="progress"),
]
