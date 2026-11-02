import json
import uuid
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts import otp
from accounts.models import ConsentRecord, User
from core import clock
from core.money import format_inr
from fees import services
from fees.models import GatewayEvent, PaymentLink, Receipt, Student
from fees.receipt_pdf import render as render_receipt_pdf
from parents.i18n import strings

CONSENT_VERSION = "2026-1"


def lang_of(request):
    if request.user.is_authenticated:
        return request.user.preferred_language
    return request.session.get("lang", "hi")


def ctx(request, **extra):
    lang = lang_of(request)
    return {"t": strings(lang), "lang": lang, **extra}


def parent_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated or user.role != User.Role.PARENT:
            return redirect("parents:login")
        pending = [s for s in user.children.all() if s.is_minor and not
                   ConsentRecord.objects.filter(parent=user, student=s, policy_version=CONSENT_VERSION).exists()]
        if pending and view.__name__ != "consent":
            return redirect("parents:consent")
        return view(request, *args, **kwargs)
    return wrapped


def my_child(request, student_id=None):
    kids = request.user.children.select_related("batch__centre", "plan")
    if student_id:
        return get_object_or_404(kids, student_id=student_id)
    child = kids.filter(student_id=request.session.get("child")).first() or kids.filter(status="active").first() or kids.first()
    if child is None:
        raise Http404
    return child


# ----------------------------------------------------------------- auth

def login_phone(request):
    if request.user.is_authenticated and request.user.role == User.Role.PARENT:
        return redirect("parents:home")
    error = None
    if request.method == "POST":
        phone = otp.normalise_phone(request.POST.get("phone", ""))
        if phone is None:
            error = "Enter a 10-digit mobile number · 10 अंकों का मोबाइल नंबर डालें"
        else:
            try:
                code = otp.request_code(phone)
            except otp.OTPError as exc:
                error = str(exc)
            else:
                request.session["otp_phone"] = phone
                if settings.DEMO_MODE and code:
                    request.session["demo_otp"] = code
                return redirect("parents:verify")
    return render(request, "parents/login.html", ctx(request, error=error, phone=request.POST.get("phone", "")))


def verify_code(request):
    phone = request.session.get("otp_phone")
    if not phone:
        return redirect("parents:login")
    error = None
    if request.method == "POST":
        try:
            user = otp.verify(phone, request.POST.get("code", ""))
        except otp.OTPError as exc:
            error = str(exc)
        else:
            request.session.pop("demo_otp", None)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("parents:home")
    masked = f"{'•' * 6}{phone[-4:]}"
    return render(request, "parents/verify.html", ctx(request, error=error, masked=masked,
                                                      demo_code=request.session.get("demo_otp")))


@parent_required
def consent(request):
    kids = [s for s in request.user.children.all() if s.is_minor]
    if request.method == "POST":
        if request.POST.get("agree") != "yes":
            messages.error(request, "Please tick the box to continue · आगे बढ़ने के लिए बॉक्स चुनें")
        else:
            for s in kids:
                ConsentRecord.objects.get_or_create(parent=request.user, student=s, policy_version=CONSENT_VERSION)
            return redirect("parents:home")
    return render(request, "parents/consent.html", ctx(request, kids=kids))


@require_POST
def sign_out(request):
    logout(request)
    return redirect("parents:login")


@require_POST
def set_language(request):
    lang = "en" if request.POST.get("lang") == "en" else "hi"
    if request.user.is_authenticated and request.user.role == User.Role.PARENT:
        request.user.preferred_language = lang
        request.user.save(update_fields=["preferred_language"])
    request.session["lang"] = lang
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "parents:home")


# ----------------------------------------------------------- dues home

@parent_required
def home(request):
    if "child" in request.GET:
        request.session["child"] = request.GET["child"]
    today = clock.today()
    child = my_child(request)
    summary = services.ledger_summary(child, today)
    live = [i for i in summary["instalments"] if not i.cancelled and i.balance > 0]
    focus = next((i for i in live if i.state(today).is_overdue), None) or (live[0] if live else None)
    link = services.get_or_create_link(focus) if focus and child.status == Student.Status.ACTIVE else None
    later = next((i for i in live if focus and i.pk != focus.pk), None)
    state = focus.state(today) if focus else None
    return render(request, "parents/home.html", ctx(
        request, child=child, children=request.user.children.all(), focus=focus, state=state, link=link,
        later=later, sum=summary, tab="dues"))


@parent_required
def receipts(request):
    child = my_child(request)
    payments = child.payments.select_related("receipt").prefetch_related("allocations__instalment")
    return render(request, "parents/receipts.html", ctx(request, child=child, payments=payments, tab="receipts"))


@parent_required
def receipt_pdf(request, number):
    r = get_object_or_404(Receipt.objects.select_related("payment__student"), number=number,
                          payment__student__parent=request.user)
    resp = HttpResponse(render_receipt_pdf(r), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="receipt-{r.number}.pdf"'
    return resp


@parent_required
def progress(request):
    child = my_child(request)
    weeks = list(child.attendance.order_by("-week_start")[:4])[::-1]
    month = list(child.attendance.order_by("-week_start")[:4])
    held = sum(w.classes_held for w in month)
    pct = round(100 * sum(w.classes_attended for w in month) / held) if held else None
    tests = list(child.scores.order_by("-test_date")[:5])
    from django.db.models import Avg
    for t in tests:
        t.batch_avg = t.__class__.objects.filter(student__batch=child.batch, test_name=t.test_name).aggregate(a=Avg("score"))["a"]
    return render(request, "parents/progress.html", ctx(request, child=child, weeks=weeks, pct=pct, tests=tests,
                                                        tab="progress"))


# ------------------------------------------------------- pay (mock gateway)

def _link(token):
    return get_object_or_404(PaymentLink.objects.select_related("instalment__plan__student__batch__centre"), token=token)


def pay(request, token):
    """Public page behind the link in reminder messages (no login needed,
    like a real gateway link). Shows only first name, batch and amount."""
    link = _link(token)
    inst = link.instalment
    student = inst.plan.student
    if request.method == "POST":
        if link.status != PaymentLink.Status.ACTIVE:
            return redirect("pay", token=token)
        outcome = request.POST.get("simulate", "success")
        amount = link.amount
        if outcome == "different":
            try:
                amount = Decimal(request.POST.get("amount", "")).quantize(Decimal("0.01"))
                if amount <= 0:
                    raise ValueError
            except Exception:
                messages.error(request, "Enter a valid test amount.")
                return redirect("pay", token=token)
        payload = {"event_id": f"evt_{uuid.uuid4().hex}", "status": "failed" if outcome == "fail" else "captured",
                   "link_token": token, "amount": str(amount), "paid_on": clock.today().isoformat(),
                   "method": request.POST.get("method", "upi"), "reference": f"TEST{uuid.uuid4().hex[:10].upper()}"}
        _deliver_webhook(payload)
        return redirect(f"/pay/{token}/done/?e={payload['event_id']}")
    return render(request, "pay/pay.html", ctx(request, link=link, inst=inst, student=student,
                                               first=student.name.split()[0], state=inst.state(clock.today())))


def _deliver_webhook(payload):
    """The mock gateway calls our webhook exactly as a real one would:
    signed JSON in, same handler. Only the network hop is skipped."""
    body = json.dumps(payload).encode()
    signature = services.sign_payload(body)
    if not services.verify_signature(body, signature):
        raise services.PaymentError("Webhook secret is not configured")
    return services.process_gateway_event(json.loads(body))


def pay_done(request, token):
    link = _link(token)
    event = GatewayEvent.objects.filter(event_id=request.GET.get("e", ""), payload__link_token=token).first()
    if event is None:
        return redirect("pay", token=token)
    receipt = None
    if event.outcome == "applied":
        receipt = (Receipt.objects.filter(payment__allocations__instalment=link.instalment,
                                          payment__reference=event.payload.get("reference")).first())
    return render(request, "pay/done.html", ctx(request, event=event, link=link, receipt=receipt,
                                                amount=format_inr(Decimal(event.payload.get("amount", "0"))),
                                                student=link.instalment.plan.student))


@csrf_exempt
@require_POST
def gateway_webhook(request):
    """Endpoint a real payment gateway would call. Signature required."""
    if not services.verify_signature(request.body, request.headers.get("X-Gateway-Signature", "")):
        return JsonResponse({"error": "bad signature"}, status=401)
    try:
        payload = json.loads(request.body)
        result = services.process_gateway_event(payload)
    except (ValueError, services.PaymentError) as exc:
        return HttpResponseBadRequest(json.dumps({"error": str(exc)}), content_type="application/json")
    return JsonResponse({"outcome": result.outcome, "message": result.message})
