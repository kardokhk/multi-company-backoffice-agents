"""Connectors: read each company's own exports into canonical records.

Adding a new company with a different stack means adding (or configuring) one parser here; the
agents downstream only ever see canonical records. In production these readers sit behind the
accounting system's API client (for example Xero, QuickBooks Online or Sage) where one exists,
with file exports as the fallback.
"""
from __future__ import annotations

import csv
import email
import email.policy
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml
from openpyxl import load_workbook
from pypdf import PdfReader

from .schema import Account, Attachment, BankTxn, Email, OpenInvoice, Posting, Vendor


# ---------------------------------------------------------------- value parsers
def parse_amount(s, decimal=".") -> float:
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d,.]", "", s)
    if decimal == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    v = float(s)
    return -v if neg else v


def parse_date(s, fmt: str) -> date:
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s).strip(), fmt).date()


def _rows(path: Path, delim=","):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=delim))


# ---------------------------------------------------------------- tenant bundle
@dataclass
class TenantData:
    config: dict
    accounts: list[Account] = field(default_factory=list)
    history: list[Posting] = field(default_factory=list)
    vendors: list[Vendor] = field(default_factory=list)
    receivables: list[OpenInvoice] = field(default_factory=list)
    bank: list[BankTxn] = field(default_factory=list)
    inbox: list[Email] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.config["id"]


# ---------------------------------------------------------------- ledgers
def read_ledger(fmt: str, d: Path, cur: str):
    if fmt == "LA":
        accts = [Account(r["Code"], r["Name"], r["Type"].lower()) for r in _rows(d / "chart_of_accounts.csv")]
        hist = [Posting(parse_date(r["Date"], "%d/%m/%Y"), r["Description"],
                        parse_amount(r["Credit"]) - parse_amount(r["Debit"]), r["AccountCode"]) for r in _rows(d / "general_ledger.csv")]
        vend = [Vendor(r["ContactName"], r["EmailAddress"].split("@")[-1], r["BankAccount"], r["DefaultAccount"])
                for r in _rows(d / "contacts.csv") if r["IsSupplier"].upper() == "TRUE"]
        ar = [OpenInvoice(r["InvoiceNumber"], r["Contact"], r["InvoiceNumber"], parse_date(r["InvoiceDate"], "%d/%m/%Y"),
                          parse_date(r["DueDate"], "%d/%m/%Y"), parse_amount(r["Total"]), r["Currency"], "AR") for r in _rows(d / "receivables.csv")]
    elif fmt == "LB":
        kinds = {"Income": "income", "Expense": "expense", "Other Current Asset": "asset", "Other Current Liability": "liability"}
        accts = [Account(r["Account"], r["Account"], kinds.get(r["Type"], "expense")) for r in _rows(d / "Account List.csv")]
        hist = [Posting(parse_date(r["Date"], "%m/%d/%Y"), r["Memo/Description"], parse_amount(r["Amount"]), r["Account"])
                for r in _rows(d / "Transaction Detail.csv")]
        vend = [Vendor(r["Vendor"], r["Email"].split("@")[-1], r["Bank Account"], r["Default Expense Account"]) for r in _rows(d / "Vendor Contact List.csv")]
        ar = [OpenInvoice(r["Num"], r["Customer"], r["Num"], parse_date(r["Date"], "%m/%d/%Y"), parse_date(r["Due Date"], "%m/%d/%Y"),
                          parse_amount(r["Open Balance"]), cur, "AR") for r in _rows(d / "Open Invoices.csv")]
    elif fmt == "LC":
        wb = load_workbook(d / "accounts_export.xlsx", read_only=True, data_only=True)
        sh = lambda n: [r for r in wb[n].iter_rows(values_only=True)]
        accts = [Account(str(c), n, k.lower()) for c, n, k in sh("Chart")[1:]]
        rows = sh("Nominal Ledger")
        hdr = next(i for i, r in enumerate(rows) if r and r[0] == "Date")
        hist = [Posting(parse_date(r[0], ""), r[2], -float(r[3]), str(r[1])) for r in rows[hdr + 1:] if r and r[0]]
        vend = [Vendor(n, e.split("@")[-1], ib, str(dflt)) for n, e, ib, dflt in sh("Suppliers")[1:]]
        ar = [OpenInvoice(ref, cu, ref, parse_date(i, ""), parse_date(du, ""), float(o), cur, "AR") for ref, cu, i, du, o in sh("Customers - Aged")[1:]]
    elif fmt == "LD":
        accts = [Account(r["Category"], r["Label"], r["Group"]) for r in _rows(d / "categories.csv")]
        hist = [Posting(parse_date(r["Txn date"], "%Y-%m-%d"), r["What"], parse_amount(r["In"]) - parse_amount(r["Out"]), r["Category"])
                for r in _rows(d / "ledger.csv")]
        vend = [Vendor(r["Supplier"], r["Email domain"], r["Pay to"], r["Usual category"]) for r in _rows(d / "suppliers.csv")]
        ar = [OpenInvoice(r["Invoice"], r["Client"], r["Invoice"], parse_date(r["Sent"], "%Y-%m-%d"), parse_date(r["Due"], "%Y-%m-%d"),
                          parse_amount(r["Amount"]), cur, "AR") for r in _rows(d / "invoices_out.csv") if r["Paid?"].lower() != "yes"]
    else:
        raise ValueError(f"unknown ledger format {fmt}")
    return accts, hist, vend, ar


# ---------------------------------------------------------------- banks
def read_bank(fmt: str, d: Path, cid: str, cur: str) -> list[BankTxn]:
    raw = []  # (date, description, amount)
    if fmt == "B1":
        f = next(d.glob("*.csv"))
        raw = [(parse_date(r["Date"], "%d/%m/%Y"), r["Description"], parse_amount(r["Amount"])) for r in _rows(f)]
    elif fmt == "B2":
        f = next(d.glob("*.csv"))
        raw = [(parse_date(r["Posted Date"], "%Y-%m-%d"), r["Payee/Description"],
                parse_amount(r["Credit"], ",") - parse_amount(r["Debit"], ",")) for r in _rows(f, ";")]
    elif fmt == "B3":
        f = next(d.glob("*.csv"))
        raw = [(parse_date(r["Transaction Date"], "%d %b %Y"), r["Transaction Description"],
                parse_amount(r["Money In"]) - parse_amount(r["Money Out"])) for r in _rows(f)]
    elif fmt == "B4":
        wb = load_workbook(next(d.glob("*.xlsx")), read_only=True, data_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        hdr = next(i for i, r in enumerate(rows) if r and r[0] == "Value Date")
        raw = [(parse_date(r[0], ""), r[1], (r[3] or 0) - (r[2] or 0)) for r in rows[hdr + 1:] if r and r[0]]
    elif fmt == "B5":
        f = next(d.glob("*.csv"))
        raw = [(parse_date(r["Date"], "%m/%d/%Y"), r["Description"], parse_amount(r["Amount"])) for r in _rows(f)]
    else:
        raise ValueError(f"unknown bank format {fmt}")
    raw.sort(key=lambda x: (x[0], x[1], round(x[2], 2)))
    return [BankTxn(f"{cid}-T{i:04d}", dt, desc, round(amt, 2), cur) for i, (dt, desc, amt) in enumerate(raw, 1)]


# ---------------------------------------------------------------- mailbox
def pdf_text(data: bytes) -> str:
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


def read_inbox(d: Path) -> list[Email]:
    out = []
    for f in sorted(d.glob("*.eml")):
        m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
        body = m.get_body(preferencelist=("plain",))
        atts = [Attachment(p.get_filename(), p.get_content_type(), p.get_content()) for p in m.iter_attachments()]
        out.append(Email(m["Message-ID"], m["From"], m["Subject"], body.get_content() if body else "", m["Date"], atts))
    return out


def load_tenant(company_dir: Path) -> TenantData:
    cfg = yaml.safe_load((company_dir / "company.yaml").read_text())
    st = cfg["stack"]
    accts, hist, vend, ar = read_ledger(st["ledger_format"], company_dir / "ledger", cfg["currency"])
    t = TenantData(cfg, accts, hist, vend, ar)
    t.bank = read_bank(st["bank_format"], company_dir / "bank", cfg["id"], cfg["currency"])
    t.inbox = read_inbox(company_dir / "inbox")
    return t
