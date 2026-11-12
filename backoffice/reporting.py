"""Monthly management pack: figures computed in code, commentary written by the model and checked."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date, timedelta

from .connectors import TenantData


def fmt(cur: str, v: float) -> str:
    return f"{cur} {v:,.2f}"


def compute_facts(t: TenantData, cat: dict[str, dict], matches: dict, ap: list[dict], pending: dict, blocked: int,
                  month_end: date) -> tuple[dict, list[float], dict]:
    cur = t.config["currency"]
    kind = {a.code: a.kind for a in t.accounts}
    name = {a.code: a.name for a in t.accounts}
    by_acct = defaultdict(float)
    for x in t.bank:
        code = cat.get(x.txn_id, {}).get("account_code")
        by_acct[code] += x.amount
    revenue = sum(v for c, v in by_acct.items() if kind.get(c) == "income")
    opex = -sum(v for c, v in by_acct.items() if kind.get(c) == "expense")
    money_in = sum(x.amount for x in t.bank if x.amount > 0)
    money_out = -sum(x.amount for x in t.bank if x.amount < 0)
    prev_start = date(month_end.year, month_end.month, 1) - timedelta(days=1)
    prev = [p for p in t.history if (p.date.year, p.date.month) == (prev_start.year, prev_start.month)]
    prev_rev = sum(p.amount for p in prev if kind.get(p.account_code) == "income")
    prev_opex = -sum(p.amount for p in prev if kind.get(p.account_code) == "expense")
    top = sorted(((name.get(c, str(c)), -v) for c, v in by_acct.items() if kind.get(c) == "expense"), key=lambda z: -z[1])[:5]

    settled = {i for m in matches.values() if m.status == "auto" for i in m.invoice_ids}
    ar_open = [i for i in t.receivables if i.invoice_id not in settled]
    ar_over = [i for i in ar_open if i.due_date < month_end]
    ap_open = [a for a in ap if a["invoice_id"] not in settled]
    ap_soon = [a for a in ap_open if a.get("due_date") and date.fromisoformat(a["due_date"]) <= month_end + timedelta(days=14)]

    pct = lambda a, b: round(100 * (a - b) / b, 1) if b else None
    raw = {"revenue": revenue, "prev_revenue": prev_rev, "revenue_change_pct": pct(revenue, prev_rev), "operating_expenses": opex,
           "prev_operating_expenses": prev_opex, "opex_change_pct": pct(opex, prev_opex), "money_in": money_in, "money_out": money_out,
           "net_cash_flow": money_in - money_out, "ar_open_count": len(ar_open), "ar_open_total": sum(i.total for i in ar_open),
           "ar_overdue_count": len(ar_over), "ar_overdue_total": sum(i.total for i in ar_over), "ap_open_count": len(ap_open),
           "ap_open_total": sum(a["total"] for a in ap_open), "ap_due_14d_count": len(ap_soon), "ap_due_14d_total": sum(a["total"] for a in ap_soon),
           "items_awaiting_review": sum(pending.values()), "high_risk_items_blocked": blocked, "top_expenses": top}
    facts = {
        "company": t.config["name"], "period": month_end.strftime("%B %Y"),
        "revenue": fmt(cur, revenue), "revenue_previous_month": fmt(cur, prev_rev),
        "revenue_change_vs_previous_month": f"{raw['revenue_change_pct']:+.1f}%" if raw["revenue_change_pct"] is not None else "n/a",
        "operating_expenses": fmt(cur, opex), "operating_expenses_previous_month": fmt(cur, prev_opex),
        "operating_expenses_change": f"{raw['opex_change_pct']:+.1f}%" if raw["opex_change_pct"] is not None else "n/a",
        "money_in": fmt(cur, money_in), "money_out": fmt(cur, money_out), "net_cash_flow": fmt(cur, money_in - money_out),
        "largest_expense_accounts": [f"{n}: {fmt(cur, v)}" for n, v in top],
        "customer_invoices_open": f"{len(ar_open)} totalling {fmt(cur, raw['ar_open_total'])}",
        "customer_invoices_overdue": f"{len(ar_over)} totalling {fmt(cur, raw['ar_overdue_total'])}",
        "supplier_invoices_unpaid": f"{len(ap_open)} totalling {fmt(cur, raw['ap_open_total'])}",
        "supplier_invoices_due_next_14_days": f"{len(ap_soon)} totalling {fmt(cur, raw['ap_due_14d_total'])}",
        "items_awaiting_your_review": sum(pending.values()),
        "high_risk_items_blocked": f"{blocked} (for example bank-detail change requests or suspicious emails)",
    }
    allowed = [v for k, v in raw.items() if isinstance(v, (int, float)) and v is not None] + [v for _, v in top]
    allowed += [2026, 14]
    return facts, allowed, raw


def render_pdf(path, t: TenantData, facts: dict, raw: dict, commentary: str, queue_rows: list[dict], check: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    cur = t.config["currency"]
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=15, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11, spaceBefore=8, spaceAfter=4)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=13 * mm, bottomMargin=12 * mm)
    el = [Paragraph(f"{t.config['name']}: management report, {facts['period']}", h1),
          Paragraph(f"{t.config['sector'].capitalize()} · currency {cur} · prepared from bank, ledger and mailbox data on close", small), Spacer(1, 6)]

    def kpi(label, value, sub=""):
        return [Paragraph(f"<font size=7.5 color='#555555'>{label}</font><br/><b>{value}</b><br/><font size=7.5 color='#555555'>{sub}</font>", body)]
    tiles = [kpi("Revenue", fmt(cur, raw["revenue"]), f"{facts['revenue_change_vs_previous_month']} vs previous month"),
             kpi("Operating expenses", fmt(cur, raw["operating_expenses"]), f"{facts['operating_expenses_change']} vs previous month"),
             kpi("Net cash flow", fmt(cur, raw["net_cash_flow"]), f"in {raw['money_in']:,.0f} / out {raw['money_out']:,.0f}"),
             kpi("Needs your decision", f"{raw['items_awaiting_review']} items", f"{raw['high_risk_items_blocked']} high-risk blocked")]
    tt = Table([tiles], colWidths=[43 * mm] * 4)
    tt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")), ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    el += [tt, Paragraph("Commentary", h2), Paragraph(commentary, body)]
    nchk = len(check["unsupported_final"])
    el.append(Paragraph(f"Every number in the commentary was checked against the computed figures ({nchk} unsupported).", small))

    # expense chart
    top = raw["top_expenses"]
    if top:
        fig, ax = plt.subplots(figsize=(6.6, 1.6), dpi=200)
        names = [n for n, _ in top][::-1]
        vals = [v for _, v in top][::-1]
        ax.barh(names, vals, color="#0072B2", height=0.6)
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v:,.0f}", va="center", fontsize=7)
        ax.set_xlabel(f"Amount ({cur})", fontsize=7.5)
        ax.tick_params(labelsize=7.5)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.set_xlim(0, max(vals) * 1.18)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        el += [Paragraph("Largest expense accounts", h2), Image(buf, width=170 * mm, height=170 * mm * 1.6 / 6.6)]

    tbl = [["Receivables and payables", "Count", f"Total ({cur})"],
           ["Customer invoices open", raw["ar_open_count"], f"{raw['ar_open_total']:,.2f}"],
           ["of which overdue at month end", raw["ar_overdue_count"], f"{raw['ar_overdue_total']:,.2f}"],
           ["Supplier invoices unpaid", raw["ap_open_count"], f"{raw['ap_open_total']:,.2f}"],
           ["of which due in the next 14 days", raw["ap_due_14d_count"], f"{raw['ap_due_14d_total']:,.2f}"]]
    t2 = Table(tbl, colWidths=[95 * mm, 25 * mm, 50 * mm])
    t2.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                            ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.black)]))
    el += [Spacer(1, 6), t2]
    if queue_rows:
        rows = [["Waiting for a decision", "Why"]] + [[Paragraph(q["title"], small), Paragraph(q["reason"], small)] for q in queue_rows[:8]]
        t3 = Table(rows, colWidths=[90 * mm, 80 * mm])
        t3.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        el += [Paragraph("Decisions needed", h2), t3]
        if len(queue_rows) > 8:
            el.append(Paragraph(f"... and {len(queue_rows) - 8} more in the approval queue.", small))
    el += [Spacer(1, 8), Paragraph("Figures are computed directly from categorised bank transactions, the ledger export and supplier invoices received "
                                   "by email; items still awaiting review are included at their proposed account. Synthetic demonstration data.", small)]
    doc.build(el)
