"""Deterministic safeguards that run around every model call.

The model proposes; these functions and the approval policy decide. Nothing here trusts model output.
"""
from __future__ import annotations

import re
from datetime import date

# ---------------------------------------------------------------- PII redaction
PATTERNS = [
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")),
    ("ACCOUNT", re.compile(r"\bUS-ACCT-\d{6,12}-RTN-\d{9}\b")),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d ()-]{8,}\d\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
]


class Redactor:
    """Swap personal and payment identifiers for placeholders before text reaches a model,
    and swap them back in the model's answer. The mapping never leaves this process."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.fwd: dict[str, str] = {}
        self.back: dict[str, str] = {}

    def redact(self, text: str) -> str:
        if not self.enabled:
            return text
        for label, pat in PATTERNS:
            def sub(m, label=label):
                v = m.group(0)
                if label == "PHONE" and re.fullmatch(r"[\d ,.]+", v) and "," in v:
                    return v
                if v not in self.fwd:
                    tok = f"[{label}_{sum(1 for k in self.back if k.startswith('[' + label)) + 1}]"
                    self.fwd[v], self.back[tok] = tok, v
                return self.fwd[v]
            text = pat.sub(sub, text)
        return text

    def restore(self, text: str) -> str:
        for tok, v in self.back.items():
            text = text.replace(tok, v)
        return text


# ---------------------------------------------------------------- prompt-injection screen
INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) instructions|system (note|prompt|message)|assistant instruction|"
    r"you are (an? )?(ai|assistant|language model)|pre-?approved|do not (send|route) (it|this) (for|to) review|"
    r"mark (it|this) as (approved|paid)|disregard (the )?(rules|policy))", re.I)


def injection_signals(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in INJECTION.finditer(text)})


# ---------------------------------------------------------------- identities
def norm_account(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def iban_valid(s: str) -> bool:
    s = norm_account(s)
    if s.startswith("US"):
        return bool(re.fullmatch(r"USACCT\d{6,12}RTN\d{9}", s))
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    return int("".join(str(int(c, 36)) for c in s[4:] + s[:4])) % 97 == 1


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lookalike(domain: str, trusted: list[str]) -> str | None:
    """Return the trusted domain that ``domain`` imitates, if any (close but not equal)."""
    d = domain.lower()
    if d in trusted:
        return None
    stem = d.split(".")[0]
    for t in trusted:
        ts = t.split(".")[0]
        if stem == ts or (len(ts) >= 5 and ts in stem) or levenshtein(stem, ts) == 1:
            return t
    return None


# ---------------------------------------------------------------- invoice checks
def invoice_checks(inv: dict, currency: str, today: date) -> list[str]:
    issues = []
    try:
        net, tax, total = float(inv.get("net") or 0), float(inv.get("tax") or 0), float(inv.get("total") or 0)
        if total <= 0:
            issues.append("missing_total")
        elif abs(net + tax - total) > 0.02:
            issues.append("amounts_do_not_add_up")
    except (TypeError, ValueError):
        issues.append("unreadable_amounts")
    try:
        iss = date.fromisoformat(inv.get("issue_date") or "")
        if iss > today:
            issues.append("issue_date_in_future")
        if inv.get("due_date"):
            if date.fromisoformat(inv["due_date"]) < iss:
                issues.append("due_before_issue")
    except ValueError:
        issues.append("unreadable_dates")
    if (inv.get("currency") or currency).upper() != currency:
        issues.append("foreign_currency")
    if not inv.get("invoice_number"):
        issues.append("missing_invoice_number")
    if inv.get("bank_account") and not iban_valid(inv["bank_account"]):
        issues.append("bank_account_fails_checksum")
    return issues


# ---------------------------------------------------------------- numbers in generated prose
NUM = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?%?")


MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"


def numbers_in(text: str) -> list[str]:
    return [m.group(0).strip() for m in NUM.finditer(text)]


def unsupported_numbers(text: str, allowed: list[float]) -> list[str]:
    """Numbers in ``text`` that do not match an allowed value at the precision written.
    Years and day numbers written next to a month name are exempt; everything else must be supported."""
    bad = []
    for m in NUM.finditer(text):
        tok = m.group(0).strip()
        v = float(tok.replace(",", "").replace("%", "").strip())
        decimals = len(tok.split(".")[1].rstrip("% ")) if "." in tok else 0
        before, after = text[max(0, m.start() - 12):m.start()].lower(), text[m.end():m.end() + 12].lower()
        if decimals == 0 and v in (2025, 2026):
            continue
        if decimals == 0 and v <= 31 and (re.search(MONTHS + r"\s*$", before) or re.match(r"\s*" + MONTHS, after)):
            continue
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(abs(a) - abs(v)) <= tol for a in allowed):
            bad.append(tok)
    return bad
