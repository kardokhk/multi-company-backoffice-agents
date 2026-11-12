"""Canonical records. Every connector converts a company's own export format into these."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class Account:
    code: str            # the company's own account code (or name, if the ledger has no codes)
    name: str
    kind: str            # income | expense | asset | liability | equity


@dataclass
class Posting:           # a historical, already-approved ledger line
    date: date
    description: str
    amount: float        # signed, from the bank's point of view: + money in, - money out
    account_code: str


@dataclass
class BankTxn:
    txn_id: str
    date: date
    description: str
    amount: float        # + in, - out
    currency: str


@dataclass
class OpenInvoice:       # customer invoice (receivable) or supplier invoice (payable)
    invoice_id: str
    party: str
    number: str
    issue_date: date
    due_date: date
    total: float
    currency: str
    side: str            # AR | AP


@dataclass
class Vendor:
    name: str
    email_domain: str
    iban: str
    default_account: str = ""


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class Email:
    message_id: str
    sender: str
    subject: str
    body: str
    received: str
    attachments: list[Attachment] = field(default_factory=list)


def to_dict(obj) -> dict:
    d = asdict(obj)
    for k, v in d.items():
        if isinstance(v, date):
            d[k] = v.isoformat()
    return d
