from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from academics import importer
from core import audit, clock
from fees import reports, rules, services
from fees.models import Batch, Instalment, Payment, Receipt, Refund, Student, UnmatchedPayment
from fees.receipt_pdf import render as render_receipt_pdf
from fees.services import PaymentError
from office import forms
from office.access import ALL_STAFF, COUNSELLOR, DIRECTOR, MONEY_ROLES, get_student, scope_by_centre, \
    scope_students, staff_required
from reminders import services as reminder_services
from reminders.models import OutboundMessage, Reminder
from risk import services as risk_services
from risk.models import CounsellorTask


class LoginView(auth_views.LoginView):
    template_name = "office/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        if not form.get_user().is_staff_role and not form.get_user().is_superuser:
            form.add_error(None, "This sign-in is for centre staff. Parents sign in with their phone number.")
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_success_url(self):
        user = self.request.user
        if user.role == COUNSELLOR:
            return reverse("office:tasks")
        return self.get_redirect_url() or reverse("office:dashboard")


def nav_counts(user):
    return {
        "unmatched": scope_by_centre(user, UnmatchedPayment.objects.filter(status="open"), "centre").count(),
        "tasks": scope_by_centre(user, CounsellorTask.objects.filter(status="open")).count(),
    }


def page(request, template, ctx=None, active=""):
    ctx = ctx or {}
    ctx.update({"active": active, "counts": nav_counts(request.user)})
    return render(request, template, ctx)


# ----------------------------------------------------------- dashboard

@staff_required(*MONEY_ROLES)
def dashboard(request):
    today = clock.today()
    students = scope_students(request.user).filter(status=Student.Status.ACTIVE)
    centre_filter = request.GET.get("centre")
    if centre_filter and request.user.sees_all_centres:
        students = students.filter(batch__centre__code=centre_filter)
    data = reports.dues_overview(students, today)
    from fees.models import Centre
    return page(request, "office/dashboard.html", {
        "d": data, "centres": Centre.objects.all() if request.user.sees_all_centres else None,
        "centre_filter": centre_filter, "export_form": forms.ExportForm(initial={
            "start": today.replace(day=1), "end": today}),
    }, "dashboard")


@staff_required(*MONEY_ROLES)
def tally_export(request):
    form = forms.ExportForm(request.GET)
    if not form.is_valid():
        messages.error(request, "Choose a valid date range for the export.")
        return redirect("office:dashboard")
    start, end = form.cleaned_data["start"], form.cleaned_data["end"]
    qs = reports.receipts_between(scope_students(request.user), start, end)
    audit.record(request.user, "export.tally", f"{start}..{end}", rows=qs.count())
    resp = HttpResponse(reports.tally_csv(qs), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="tally-receipts-{start}-to-{end}.csv"'
    return resp


# ------------------------------------------------------------ students

STATUS_FILTERS = {"overdue": "Overdue", "due_soon": "Due in 14 days", "clear": "Nothing overdue", "withdrawn": "Withdrawn"}


@staff_required(*MONEY_ROLES)
def students(request):
    today = clock.today()
    qs = scope_students(request.user).select_related("batch__centre", "plan")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(student_id__icontains=q))
    batch_id = request.GET.get("batch")
    if batch_id and batch_id.isdigit():
        qs = qs.filter(batch_id=int(batch_id))
    status = request.GET.get("status", "")
    qs = qs.filter(status=Student.Status.WITHDRAWN) if status == "withdrawn" else qs.filter(status=Student.Status.ACTIVE)

    insts = Instalment.objects.filter(plan__student__in=qs, cancelled=False).values(
        "plan__student_id", "due_date", "amount", "paid_amount")
    info = {}
    for i in insts:
        row = info.setdefault(i["plan__student_id"], {"balance": Decimal(0), "overdue": Decimal(0), "days": 0, "next": None})
        bal = i["amount"] - i["paid_amount"]
        if bal <= 0:
            continue
        row["balance"] += bal
        if i["due_date"] < today:
            row["overdue"] += bal
            row["days"] = max(row["days"], (today - i["due_date"]).days)
        elif row["next"] is None or i["due_date"] < row["next"]:
            row["next"] = i["due_date"]

    rows = []
    for s in qs:
        r = info.get(s.pk, {"balance": Decimal(0), "overdue": Decimal(0), "days": 0, "next": None})
        due_soon = r["next"] is not None and (r["next"] - today).days <= 14
        if status == "overdue" and not r["overdue"]:
            continue
        if status == "due_soon" and not due_soon:
            continue
        if status == "clear" and r["overdue"]:
            continue
        rows.append({"s": s, **r, "bucket": rules.ageing_bucket(r["days"]), "due_soon": due_soon})
    rows.sort(key=lambda r: (-r["days"], r["s"].name))

    pager = Paginator(rows, 25).get_page(request.GET.get("page"))
    batches = scope_by_centre(request.user, Batch.objects.select_related("centre"), "centre")
    return page(request, "office/students.html", {
        "page_obj": pager, "q": q, "batches": batches, "batch_id": batch_id, "status": status,
        "status_filters": STATUS_FILTERS, "total": len(rows),
    }, "students")


@staff_required(*MONEY_ROLES)
def ledger(request, student_id):
    today = clock.today()
    student = get_student(request.user, student_id)
    summary = services.ledger_summary(student, today)
    insts = summary["instalments"]
    focus = next((i for i in insts if not i.cancelled and i.balance > 0 and i.state(today).is_overdue), None) \
        or next((i for i in insts if not i.cancelled and i.balance > 0), None)
    return page(request, "office/ledger.html", {
        "student": student, "sum": summary,
        "rows": [{"i": i, "state": i.state(today)} for i in insts],
        "payments": student.payments.select_related("receipt").prefetch_related("allocations__instalment"),
        "focus": focus,
        "reminders": Reminder.objects.filter(instalment=focus).order_by("send_on") if focus else [],
        "messages_out": student.messages.all()[:6],
        "task": student.counsellor_tasks.filter(status="open").select_related("assessment").first(),
        "pay_form": forms.OfflinePaymentForm(initial={"received_on": today, "amount": summary["overdue_amount"] or None}),
        "refund": getattr(student, "refund", None),
        "can_pay": student.status == Student.Status.ACTIVE and summary["balance"] > 0,
    }, "students")


@staff_required(*MONEY_ROLES)
@require_POST
def record_payment(request, student_id):
    student = get_student(request.user, student_id)
    form = forms.OfflinePaymentForm(request.POST)
    if not form.is_valid():
        errors = "; ".join(f"{form.fields[k].label or k}: {v[0]}" if k != "__all__" else v[0]
                           for k, v in form.errors.items())
        messages.error(request, f"Payment not recorded. {errors}")
        return redirect("office:ledger", student_id)
    d = form.cleaned_data
    try:
        payment = services.apply_payment(student=student, amount=d["amount"], method=d["method"],
                                         source=Payment.Source.OFFLINE, received_on=d["received_on"],
                                         reference=d["reference"], recorded_by=request.user)
    except PaymentError as exc:
        messages.error(request, f"Payment not recorded. {exc}")
    else:
        messages.success(request, f"Recorded ₹{payment.amount:,.0f}. Receipt {payment.receipt.number} issued and texted to the parent.")
    return redirect("office:ledger", student_id)


@staff_required(*MONEY_ROLES)
@require_POST
def send_link(request, student_id, number):
    student = get_student(request.user, student_id)
    inst = get_object_or_404(Instalment, plan__student=student, number=number)
    try:
        link = services.get_or_create_link(inst)
    except PaymentError as exc:
        messages.error(request, str(exc))
    else:
        reminder_services.send_pay_link(inst, link)
        audit.record(request.user, "link.sent", student.student_id, instalment=number, amount=link.amount)
        messages.success(request, f"Payment link for instalment {number} texted to {student.parent.phone}.")
    return redirect("office:ledger", student_id)


@staff_required(*ALL_STAFF)
def receipt(request, number):
    r = get_object_or_404(Receipt.objects.select_related("payment__student__batch__centre"), number=number)
    get_student(request.user, r.payment.student.student_id)  # scope check
    if request.user.role == COUNSELLOR:
        raise PermissionDenied
    resp = HttpResponse(render_receipt_pdf(r), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="receipt-{r.number}.pdf"'
    return resp


# -------------------------------------------------------------- refunds

@staff_required(*MONEY_ROLES)
def refunds(request):
    qs = scope_by_centre(request.user, Refund.objects.select_related("student__batch", "approved_by"))
    return page(request, "office/refunds.html", {"refunds": qs.order_by("-created_at")}, "refunds")


@staff_required(*MONEY_ROLES)
def refund_new(request, student_id):
    today = clock.today()
    student = get_student(request.user, student_id)
    if hasattr(student, "refund"):
        messages.info(request, "A refund has already been approved for this student.")
        return redirect("office:refunds")
    data = request.POST if request.method == "POST" else (request.GET if "last_day" in request.GET else None)
    form = forms.RefundForm(data, initial={"last_day": today})
    result = policy = error = None
    if form.is_bound and form.is_valid():
        try:
            result, policy = services.preview_refund(student, form.cleaned_data["last_day"], today)
        except rules.RuleError as exc:
            error = str(exc)
        if request.method == "POST" and result is not None:
            try:
                refund = services.approve_refund(student, form.cleaned_data["last_day"], today,
                                                 form.cleaned_data["reason"], request.user)
            except (PaymentError, rules.RuleError) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Refund of ₹{refund.refund_due:,.0f} approved for {student.name}. "
                                          f"Pay by {refund.pay_by:%d %b %Y}. Remaining instalments and reminders cancelled.")
                return redirect("office:refunds")
    paid = sum((i.paid_amount for i in Instalment.objects.filter(plan__student=student)), Decimal(0))
    return page(request, "office/refund_new.html", {
        "student": student, "form": form, "result": result, "policy": policy, "error": error, "paid": paid,
    }, "refunds")


# ------------------------------------------------------------ unmatched

@staff_required(*MONEY_ROLES)
def unmatched(request):
    qs = scope_by_centre(request.user, UnmatchedPayment.objects.select_related("suggested_student", "resolved_payment__student",
                                                                               "resolved_by"), "centre")
    return page(request, "office/unmatched.html", {
        "open": qs.filter(status="open"), "resolved": qs.exclude(status="open")[:10],
    }, "unmatched")


@staff_required(*MONEY_ROLES)
@require_POST
def unmatched_resolve(request, pk):
    item = get_object_or_404(scope_by_centre(request.user, UnmatchedPayment.objects.all(), "centre"), pk=pk)
    form = forms.ResolveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a student ID.")
        return redirect("office:unmatched")
    student = scope_students(request.user).filter(student_id=form.cleaned_data["student_id"]).first()
    if student is None:
        messages.error(request, f"No student {form.cleaned_data['student_id']} at your centre.")
        return redirect("office:unmatched")
    try:
        payment = services.resolve_unmatched(item, student, request.user)
    except PaymentError as exc:
        messages.error(request, f"Not matched. {exc}")
    else:
        messages.success(request, f"Matched to {student.name}. Receipt {payment.receipt.number} issued.")
    return redirect("office:unmatched")


# ------------------------------------------------------- counsellor tasks

@staff_required(COUNSELLOR, DIRECTOR)
def tasks(request, pk=None):
    qs = scope_by_centre(request.user, CounsellorTask.objects.select_related("student__batch", "assessment"))
    open_tasks = list(qs.filter(status="open").order_by("-assessment__score"))
    task = get_object_or_404(qs, pk=pk) if pk else (open_tasks[0] if open_tasks else None)
    from risk import model
    reasons = []
    if task:
        # Points are shares of (score - typical score); show the factors that
        # pushed this student up, scaled against the largest one.
        reasons = [c for c in task.assessment.contributions if c.get("points", 0) >= 0.01 and c.get("label")]
        top = max((c["points"] for c in reasons), default=1) or 1
        for c in reasons:
            c["width"] = round(min(100, c["points"] / top * 100))
    closed = qs.exclude(status="open").order_by("-closed_at")[:8]
    return page(request, "office/tasks.html", {
        "open_tasks": open_tasks, "task": task, "reasons": reasons, "closed": closed,
        "form": forms.CloseTaskForm(), "model": model.load(), "baseline": model.baseline(),
        "false_count": qs.filter(status="false_flag").count(),
    }, "tasks")


@staff_required(COUNSELLOR, DIRECTOR)
@require_POST
def task_close(request, pk):
    task = get_object_or_404(scope_by_centre(request.user, CounsellorTask.objects.all()), pk=pk)
    false_flag = request.POST.get("action") == "false_flag"
    form = forms.CloseTaskForm(request.POST)
    if not false_flag and not form.is_valid():
        messages.error(request, "Choose a call outcome.")
        return redirect("office:task", pk)
    try:
        risk_services.close_task(task, user=request.user, false_flag=false_flag,
                                 outcome=form.data.get("outcome", ""), notes=form.data.get("notes", "")[:1000])
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Marked as a false flag. It will be used to improve the model." if false_flag
                         else f"Call recorded for {task.student.name}.")
    return redirect("office:tasks")


# --------------------------------------------------------------- upload

@staff_required(*MONEY_ROLES)
def upload(request):
    batches = scope_by_centre(request.user, Batch.objects.select_related("centre"), "centre")
    preview = request.session.get("upload_preview")
    if request.method == "POST" and request.POST.get("step") == "confirm" and preview:
        batch = get_object_or_404(batches, pk=preview["batch"])
        n = importer.save(preview["ok"], preview["kind"], batch, test_name=preview.get("test_name"),
                          test_date=date.fromisoformat(preview["test_date"]) if preview.get("test_date") else None,
                          max_score=Decimal(preview["max_score"]) if preview.get("max_score") else None,
                          week_start=date.fromisoformat(preview["week_start"]) if preview.get("week_start") else None)
        audit.record(request.user, f"upload.{preview['kind']}", batch.name, rows=n)
        del request.session["upload_preview"]
        messages.success(request, f"Imported {n} rows for {batch.name}. Parents and students can see them now.")
        return redirect("office:upload")
    if request.method == "POST" and request.POST.get("step") == "cancel":
        request.session.pop("upload_preview", None)
        return redirect("office:upload")

    form = forms.UploadForm(request.POST or None, request.FILES or None, batches=batches)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        ok, errors = importer.parse(d["file"].read(), d["kind"], d["batch"], d.get("max_score"))
        preview = {"batch": d["batch"].pk, "batch_name": d["batch"].name, "kind": d["kind"], "ok": ok,
                   "errors": errors, "file": d["file"].name, "test_name": d.get("test_name"),
                   "test_date": d["test_date"].isoformat() if d.get("test_date") else None,
                   "max_score": str(d["max_score"]) if d.get("max_score") else None,
                   "week_start": d["week_start"].isoformat() if d.get("week_start") else None}
        request.session["upload_preview"] = preview
        return redirect("office:upload")
    return page(request, "office/upload.html", {"form": form, "preview": preview}, "upload")


@staff_required(*MONEY_ROLES)
def upload_template(request, kind):
    if kind not in importer.COLUMNS:
        raise Http404
    resp = HttpResponse(",".join(importer.COLUMNS[kind]) + "\n", content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{kind}-template.csv"'
    return resp


# ------------------------------------------------------------ messages

@staff_required(*MONEY_ROLES)
def outbox(request):
    qs = scope_by_centre(request.user, OutboundMessage.objects.select_related("student").exclude(kind="otp"))
    pager = Paginator(qs, 30).get_page(request.GET.get("page"))
    return page(request, "office/outbox.html", {"page_obj": pager}, "outbox")


# ----------------------------------------------------------------- demo

@staff_required(*MONEY_ROLES)
def demo(request):
    if not settings.DEMO_MODE:
        raise Http404
    form = forms.DemoClockForm(request.POST or None, initial={"date": clock.today()})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "set_date" and form.is_valid():
            clock.set_today(form.cleaned_data["date"])
            messages.success(request, f"Today is now {form.cleaned_data['date']:%d %b %Y} for this demo.")
        elif action == "next_day":
            clock.set_today(clock.today() + timedelta(days=1))
            messages.success(request, f"Moved to {clock.today():%d %b %Y}.")
        if action in {"run", "next_day"}:
            sent = reminder_services.dispatch_due(clock.today())
            scored = risk_services.score_all(clock.today())
            messages.info(request, f"Daily jobs ran: {sent['sent']} reminder(s) sent, {sent['cancelled']} cancelled "
                                   f"because the balance was cleared; {scored.get('flag', 0)} new counsellor task(s).")
        audit.record(request.user, f"demo.{action}", str(clock.today()))
        return redirect("office:demo")
    upcoming = scope_by_centre(request.user, Reminder.objects.filter(status="scheduled", send_on__gte=clock.today())
                               .select_related("instalment__plan__student"), "instalment__plan__student__batch__centre")[:8]
    return page(request, "office/demo.html", {"form": form, "upcoming": upcoming}, "demo")
