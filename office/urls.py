from django.contrib.auth import views as auth_views
from django.urls import path

from office import views

app_name = "office"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="office:login"), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("export/tally.csv", views.tally_export, name="tally_export"),
    path("students/", views.students, name="students"),
    path("students/<str:student_id>/", views.ledger, name="ledger"),
    path("students/<str:student_id>/pay/", views.record_payment, name="record_payment"),
    path("students/<str:student_id>/link/<int:number>/", views.send_link, name="send_link"),
    path("students/<str:student_id>/refund/", views.refund_new, name="refund_new"),
    path("receipts/<str:number>.pdf", views.receipt, name="receipt"),
    path("refunds/", views.refunds, name="refunds"),
    path("unmatched/", views.unmatched, name="unmatched"),
    path("unmatched/<int:pk>/resolve/", views.unmatched_resolve, name="unmatched_resolve"),
    path("tasks/", views.tasks, name="tasks"),
    path("tasks/<int:pk>/", views.tasks, name="task"),
    path("tasks/<int:pk>/close/", views.task_close, name="task_close"),
    path("upload/", views.upload, name="upload"),
    path("upload/template/<str:kind>.csv", views.upload_template, name="upload_template"),
    path("messages/", views.outbox, name="outbox"),
    path("demo/", views.demo, name="demo"),
]
