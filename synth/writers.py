"""Write canonical synthetic records out in each company's own export format.

Four accounting-export profiles (LA-LD) and five bank-statement formats (B1-B5) loosely modelled on
the variety seen in practice: different column names, date orders, decimal separators, sign
conventions, file types and header junk. They are not copies of any vendor's exact export.
"""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook


def _csv(path: Path, header, rows, delim=","):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=delim)
        w.writerow(header)
        w.writerows(rows)


def eu_num(x: float) -> str:
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def write_ledger(fmt, d: Path, coa, hist, open_ar, vendors):
    if fmt == "LA":
        _csv(d / "chart_of_accounts.csv", ["Code", "Name", "Type"], [[a["code"], a["name"], a["kind"].title()] for a in coa])
        _csv(d / "general_ledger.csv", ["Date", "Description", "Reference", "AccountCode", "Debit", "Credit"],
             [[h["date"].strftime("%d/%m/%Y"), h["description"], "", h["account_code"],
               f"{-h['amount']:.2f}" if h["amount"] < 0 else "", f"{h['amount']:.2f}" if h["amount"] > 0 else ""] for h in hist])
        _csv(d / "contacts.csv", ["ContactName", "EmailAddress", "BankAccount", "DefaultAccount", "IsSupplier"],
             [[v["name"], "accounts@" + v["email_domain"], v["iban"], v["default_account"], "TRUE"] for v in vendors])
        _csv(d / "receivables.csv", ["InvoiceNumber", "Contact", "InvoiceDate", "DueDate", "Total", "Currency", "Status"],
             [[a["number"], a["party"], a["issue_date"].strftime("%d/%m/%Y"), a["due_date"].strftime("%d/%m/%Y"), f"{a['total']:.2f}",
               a["currency"], "AUTHORISED"] for a in open_ar])
    elif fmt == "LB":
        _csv(d / "Account List.csv", ["Account", "Type"], [[a["name"], {"income": "Income", "expense": "Expense", "asset": "Other Current Asset",
                                                                          "liability": "Other Current Liability"}[a["kind"]]] for a in coa])
        _csv(d / "Transaction Detail.csv", ["Date", "Transaction Type", "Memo/Description", "Account", "Amount"],
             [[h["date"].strftime("%m/%d/%Y"), "Expense" if h["amount"] < 0 else "Deposit", h["description"], h["account_code"],
               f"{h['amount']:.2f}"] for h in hist])
        _csv(d / "Vendor Contact List.csv", ["Vendor", "Email", "Bank Account", "Default Expense Account"],
             [[v["name"], "accounts@" + v["email_domain"], v["iban"], v["default_account"]] for v in vendors])
        _csv(d / "Open Invoices.csv", ["Num", "Customer", "Date", "Due Date", "Open Balance"],
             [[a["number"], a["party"], a["issue_date"].strftime("%m/%d/%Y"), a["due_date"].strftime("%m/%d/%Y"), f"{a['total']:.2f}"] for a in open_ar])
    elif fmt == "LC":
        wb = Workbook()
        ws = wb.active
        ws.title = "Chart"
        ws.append(["Nominal Code", "Nominal Name", "Category"])
        for a in coa:
            ws.append([int(a["code"]) if a["code"].isdigit() else a["code"], a["name"], a["kind"].upper()])
        ws = wb.create_sheet("Nominal Ledger")
        ws.append(["Exported nominal activity"])
        ws.append([])
        ws.append(["Date", "Nominal Code", "Details", "Net Amount"])
        for h in hist:  # debit-positive convention: money out of the bank is a positive debit to the nominal
            ws.append([h["date"], int(h["account_code"]) if h["account_code"].isdigit() else h["account_code"], h["description"], -h["amount"]])
        ws = wb.create_sheet("Suppliers")
        ws.append(["Supplier Name", "E-mail", "IBAN / Account", "Default Nominal"])
        for v in vendors:
            ws.append([v["name"], "accounts@" + v["email_domain"], v["iban"], v["default_account"]])
        ws = wb.create_sheet("Customers - Aged")
        ws.append(["Ref", "Customer", "Invoice Date", "Due", "Outstanding"])
        for a in open_ar:
            ws.append([a["number"], a["party"], a["issue_date"], a["due_date"], a["total"]])
        wb.save(d / "accounts_export.xlsx")
    elif fmt == "LD":
        _csv(d / "categories.csv", ["Category", "Label", "Group"], [[a["code"], a["name"], a["kind"]] for a in coa])
        _csv(d / "ledger.csv", ["Txn date", "Category", "What", "In", "Out"],
             [[h["date"].isoformat(), h["account_code"], h["description"], f"{h['amount']:.2f}" if h["amount"] > 0 else "",
               f"{-h['amount']:.2f}" if h["amount"] < 0 else ""] for h in hist])
        _csv(d / "suppliers.csv", ["Supplier", "Email domain", "Pay to", "Usual category"],
             [[v["name"], v["email_domain"], v["iban"], v["default_account"]] for v in vendors])
        _csv(d / "invoices_out.csv", ["Invoice", "Client", "Sent", "Due", "Amount", "Paid?"],
             [[a["number"], a["party"], a["issue_date"].isoformat(), a["due_date"].isoformat(), f"{a['total']:.2f}", "no"] for a in open_ar])
    else:
        raise ValueError(fmt)


def write_bank(fmt, d: Path, stem, rows, cur, holder, r):
    bal = round(r.uniform(15000, 60000), 2)
    with_bal = []
    for x in rows:
        bal = round(bal + x["amount"], 2)
        with_bal.append((x, bal))
    if fmt == "B1":
        _csv(d / f"{stem}.csv", ["Date", "Description", "Amount", "Balance"],
             [[x["date"].strftime("%d/%m/%Y"), x["description"], f"{x['amount']:.2f}", f"{b:.2f}"] for x, b in with_bal])
    elif fmt == "B2":
        _csv(d / f"{stem}.csv", ["Posted Date", "Payee/Description", "Debit", "Credit", "Currency"],
             [[x["date"].isoformat(), x["description"], eu_num(-x["amount"]) if x["amount"] < 0 else "",
               eu_num(x["amount"]) if x["amount"] > 0 else "", cur] for x, b in with_bal], delim=";")
    elif fmt == "B3":
        _csv(d / f"{stem}.csv", ["Transaction Date", "Transaction Description", "Money Out", "Money In", "Balance"],
             [[x["date"].strftime("%d %b %Y"), x["description"], f"{-x['amount']:.2f}" if x["amount"] < 0 else "",
               f"{x['amount']:.2f}" if x["amount"] > 0 else "", f"{b:.2f}"] for x, b in with_bal])
    elif fmt == "B4":
        wb = Workbook()
        ws = wb.active
        ws.title = "Statement"
        ws.append([f"Account holder: {holder}"])
        ws.append([f"Statement period: 01/10/2026 - 31/10/2026   Currency: {cur}"])
        ws.append([])
        ws.append(["Value Date", "Narrative", "Debit", "Credit", "Running Balance"])
        for x, b in reversed(with_bal):  # newest first, as many online banks export
            ws.append([x["date"], x["description"], -x["amount"] if x["amount"] < 0 else None, x["amount"] if x["amount"] > 0 else None, b])
        wb.save(d / f"{stem}.xlsx")
    elif fmt == "B5":
        def usd(v):
            return f"(${-v:,.2f})" if v < 0 else f"${v:,.2f}"
        _csv(d / f"{stem}.csv", ["Date", "Description", "Amount", "Running Bal."],
             [[x["date"].strftime("%m/%d/%Y"), x["description"], usd(x["amount"]), usd(b)] for x, b in reversed(with_bal)])
    else:
        raise ValueError(fmt)
