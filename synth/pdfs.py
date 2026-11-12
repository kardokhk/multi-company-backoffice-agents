"""Render synthetic supplier invoices as PDFs in four layouts with different labels and formats."""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def _fmt_money(x: float, eu: bool) -> str:
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if eu else s


def render_invoice_pdf(tpl, inv, sup, pay_account, cur, country, injection, r) -> bytes:
    buf = io.BytesIO()
    size = LETTER if country == "US" else A4
    c = canvas.Canvas(buf, pagesize=size, invariant=1)
    W, H = size
    eu = tpl == "T4"
    sym = {"EUR": "EUR", "GBP": "GBP", "USD": "USD"}[cur]
    d_iss, d_due = inv["issue_date"], inv["due_date"]
    tax_label = "Sales tax" if country == "US" else "VAT"
    rate_pct = f"{inv['rate'] * 100:g}%"
    y = H - 25 * mm

    def line(txt, x=20 * mm, size_=10, bold=False):
        nonlocal y
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size_)
        c.drawString(x, y, txt)
        y -= size_ * 1.5

    if tpl == "T1":
        line(sup["legal"], size_=15, bold=True)
        line(f"accounts@{sup['domain']}")
        y -= 6
        line("INVOICE", size_=13, bold=True)
        line(f"Invoice No: {inv['number']}")
        line(f"Invoice Date: {d_iss.strftime('%d/%m/%Y')}")
        line(f"Due Date: {d_due.strftime('%d/%m/%Y')}")
    elif tpl == "T2":
        line("TAX INVOICE", size_=14, bold=True)
        line(f"From: {sup['legal']}")
        line(f"Inv # {inv['number']}    Date {d_iss.strftime('%d %B %Y')}")
        line(f"Payment terms: {inv['terms']} days from invoice date")
    elif tpl == "T3":
        line(sup["legal"].upper(), size_=14, bold=True)
        line("BILL TO: Accounts Payable")
        line(f"Invoice Number   {inv['number']}")
        line(f"Invoice Date     {d_iss.strftime('%m/%d/%Y')}")
        line(f"Payment Due      {d_due.strftime('%m/%d/%Y')}")
    else:
        line(sup["legal"], size_=13, bold=True)
        line(f"Document no. {inv['number']}")
        line(f"Issued {d_iss.strftime('%d.%m.%Y')}   Pay by {d_due.strftime('%d.%m.%Y')}")
    y -= 8
    line("Description" + " " * 40 + "Qty" + " " * 10 + "Unit" + " " * 12 + "Amount", bold=True)
    for desc, q, u in inv["lines"]:
        c.setFont("Helvetica", 10)
        c.drawString(20 * mm, y, desc)
        c.drawRightString(115 * mm, y, str(q))
        c.drawRightString(145 * mm, y, _fmt_money(u, eu))
        c.drawRightString(185 * mm, y, _fmt_money(q * u, eu))
        y -= 15
    y -= 6
    labels = {"T1": ("Subtotal", f"{tax_label} @ {rate_pct}", f"Total {sym}"),
              "T2": ("Net amount", f"{tax_label} ({rate_pct})", f"Amount payable ({sym})"),
              "T3": ("Subtotal", f"{tax_label} {rate_pct}", "Amount Due"),
              "T4": ("Net", f"{tax_label} {rate_pct}", f"Balance {sym}")}[tpl]
    if inv["rate"] == 0:
        labels = (labels[0], f"{tax_label} exempt", labels[2])
    for lab, val in zip(labels, (inv["net"], inv["vat"], inv["total"])):
        c.setFont("Helvetica-Bold" if lab == labels[2] else "Helvetica", 10)
        c.drawRightString(150 * mm, y, lab)
        c.drawRightString(185 * mm, y, (("$" if cur == "USD" and tpl == "T3" else "") + _fmt_money(val, eu)))
        y -= 15
    y -= 12
    if country == "US":
        line(f"Remit to: {pay_account}")
    else:
        acct = " ".join(pay_account[i:i + 4] for i in range(0, len(pay_account), 4))
        line(f"Bank: IBAN {acct}")
    line(f"Please quote {inv['number']} with your payment.")
    if injection:  # hidden from a human reader (white, tiny) but present in the extracted text
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica", 3)
        c.drawString(20 * mm, 12 * mm, injection)
    c.showPage()
    c.save()
    return buf.getvalue()
