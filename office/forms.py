from datetime import timedelta
from decimal import Decimal

from django import forms

from core import clock
from fees.models import Payment

REFUND_REASONS = ["Moving city", "Joined another institute", "Health", "Financial", "Other"]
CALL_OUTCOMES = ["Reached parent — support plan agreed", "Reached parent — student staying, no action needed",
                 "Reached parent — student likely to leave", "No answer — will retry"]


class DateInput(forms.DateInput):
    input_type = "date"


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "input"
            field.widget.attrs.setdefault("class", css)


class OfflinePaymentForm(StyledForm):
    amount = forms.DecimalField(min_value=Decimal("1"), max_digits=10, decimal_places=2,
                                widget=forms.NumberInput(attrs={"inputmode": "decimal", "step": "1"}))
    method = forms.ChoiceField(choices=[(Payment.Method.CASH, "Cash"), (Payment.Method.CHEQUE, "Cheque"),
                                        (Payment.Method.UPI, "UPI at counter"), (Payment.Method.BANK, "Bank transfer")])
    received_on = forms.DateField(widget=DateInput)
    reference = forms.CharField(max_length=64, required=False, help_text="Cheque or transaction number")

    def clean_received_on(self):
        d = self.cleaned_data["received_on"]
        if d > clock.today():
            raise forms.ValidationError("Date cannot be in the future.")
        if d < clock.today() - timedelta(days=90):
            raise forms.ValidationError("Older than 90 days — record it through accounts adjustment instead.")
        return d

    def clean(self):
        data = super().clean()
        if data.get("method") in {Payment.Method.CHEQUE, Payment.Method.BANK} and not data.get("reference"):
            self.add_error("reference", "Add the cheque or transaction number.")
        return data


class RefundForm(StyledForm):
    last_day = forms.DateField(widget=DateInput, label="Last day attended")
    reason = forms.ChoiceField(choices=[(r, r) for r in REFUND_REASONS])

    def clean_last_day(self):
        d = self.cleaned_data["last_day"]
        if d > clock.today():
            raise forms.ValidationError("Last day attended cannot be in the future.")
        return d


class ResolveForm(StyledForm):
    student_id = forms.CharField(max_length=16, label="Student ID")

    def clean_student_id(self):
        return self.cleaned_data["student_id"].strip().upper()


class CloseTaskForm(StyledForm):
    outcome = forms.ChoiceField(choices=[(o, o) for o in CALL_OUTCOMES])
    notes = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))


class UploadForm(StyledForm):
    KIND_CHOICES = [("scores", "Test scores"), ("attendance", "Weekly attendance")]
    batch = forms.ModelChoiceField(queryset=None)
    kind = forms.ChoiceField(choices=KIND_CHOICES, label="Type")
    test_name = forms.CharField(max_length=80, required=False)
    test_date = forms.DateField(required=False, widget=DateInput)
    max_score = forms.DecimalField(required=False, min_value=1, max_value=1000)
    week_start = forms.DateField(required=False, widget=DateInput, label="Week starting")
    file = forms.FileField(help_text="CSV, up to 1 MB")

    MAX_BYTES = 1024 * 1024

    def __init__(self, *args, batches=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["batch"].queryset = batches
        self.fields["file"].widget.attrs.update({"accept": ".csv,text/csv", "class": "input", "style": "padding:8px"})

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > self.MAX_BYTES:
            raise forms.ValidationError("File is larger than 1 MB.")
        if not f.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a .csv file.")
        return f

    def clean(self):
        data = super().clean()
        if data.get("kind") == "scores":
            for name in ("test_name", "test_date", "max_score"):
                if not data.get(name):
                    self.add_error(name, "Needed for test scores.")
        elif data.get("kind") == "attendance" and not data.get("week_start"):
            self.add_error("week_start", "Needed for attendance.")
        return data


class DemoClockForm(StyledForm):
    date = forms.DateField(widget=DateInput)


class ExportForm(StyledForm):
    start = forms.DateField(widget=DateInput, label="From")
    end = forms.DateField(widget=DateInput, label="To")

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            raise forms.ValidationError("'From' must be on or before 'To'.")
        return data
