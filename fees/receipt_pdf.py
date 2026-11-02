"""Render a GST receipt as a one-page PDF (ReportLab, no system deps)."""
from io import BytesIO

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from core.money import format_inr

INK, MUTED, LINE = HexColor("#16191D"), HexColor("#5A6370"), HexColor("#E6E8EB")


def _rs(v, decimals=True):
    # The built-in PDF fonts have no ₹ glyph; "Rs." keeps the PDF font-free.
    return format_inr(v, decimals=decimals).replace("₹", "Rs. ")


def render(receipt) -> bytes:
    p = receipt.payment
    s = p.student
    centre = s.batch.centre
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    x, y = 16 * mm, h - 20 * mm

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x, y, "Company A — Fee receipt")
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    y -= 6 * mm
    c.drawString(x, y, f"{centre.name} · GSTIN {centre.gstin or '[INSTITUTE GSTIN]'} · SAC 999293")

    rows = [
        ("Receipt no.", receipt.number),
        ("Date", p.received_on.strftime("%d %b %Y")),
        ("Student", f"{s.name} ({s.student_id})"),
        ("Batch", s.batch.name),
        ("Paid by", p.get_method_display() + (f" · ref {p.reference}" if p.reference else "")),
        ("For", ", ".join(f"Instalment {a.instalment.number}" for a in p.allocations.select_related("instalment"))),
    ]
    y -= 10 * mm
    for label, value in rows:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(x, y, label)
        c.setFillColor(INK)
        c.setFont("Helvetica", 10)
        c.drawString(x + 32 * mm, y, value)
        y -= 6.5 * mm

    y -= 2 * mm
    c.setStrokeColor(LINE)
    c.line(x, y, w - x, y)
    y -= 7 * mm
    for label, value, bold in [("Taxable value", receipt.taxable_value, False), ("CGST 9%", receipt.cgst, False),
                               ("SGST 9%", receipt.sgst, False), ("Total received", receipt.total, True)]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
        c.setFillColor(INK if bold else MUTED)
        c.drawString(x, y, label)
        c.setFillColor(INK)
        c.drawRightString(w - x, y, _rs(value))
        y -= 6.5 * mm

    c.setFont("Helvetica", 8)
    c.setFillColor(MUTED)
    c.drawString(x, 14 * mm, "Fees are GST-inclusive. This is a computer-generated receipt and needs no signature.")
    c.showPage()
    c.save()
    return buf.getvalue()
