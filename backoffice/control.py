"""Approval policy, per-tenant store and tamper-evident audit log.

Policy is code and configuration, never model output. Each company has its own SQLite file, so one
company's records cannot appear in another's queries, backups or exports.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HIGH_RISK = {"payment_risk", "prompt_injection", "lookalike_sender_domain", "bank_details_mismatch"}
NEEDS_REVIEW = {"amounts_do_not_add_up", "unreadable_amounts", "unreadable_dates", "due_before_issue", "issue_date_in_future",
                "foreign_currency", "missing_invoice_number", "missing_total", "bank_account_fails_checksum", "new_supplier",
                "unreadable_model_output"}

# Model suggestions are not applied automatically by default: on the development companies, items the model
# rated "high" confidence were wrong too often (see docs/EVALUATION.md). A company can opt in once its own
# shadow-mode sample supports it.
DEFAULT_POLICY = {"model_auto_confidence": [], "model_auto_max_amount": 500.0}


def email_decision(cat: str, flags: list[str]) -> tuple[str, str]:
    f = set(flags)
    if f & HIGH_RISK or cat == "payment_risk":
        return "blocked", "high risk: " + ", ".join(sorted(f & HIGH_RISK) or ["payment request"])
    if cat == "supplier_invoice":
        if "duplicate" in f:
            return "held", "duplicate of an invoice already received"
        if f & NEEDS_REVIEW:
            return "pending", "check: " + ", ".join(sorted(f & NEEDS_REVIEW))
        return "auto_applied", "draft bill created (payment still needs approval in the bank)"
    if cat == "admin_request":
        return "auto_applied", "task created for the office manager"
    return "auto_applied", {"receipt": "filed", "remittance": "logged for matching", "other": "archived"}.get(cat, "archived")


def txn_decision(prop: dict, amount: float, policy: dict) -> tuple[str, str]:
    p = {**DEFAULT_POLICY, **(policy or {})}
    if prop.get("account_code") is None:
        return "pending", "no valid account proposed"
    if prop["source"] in ("matched_invoice", "memory"):
        return "auto_applied", f"{prop['source'].replace('_', ' ')}"
    if prop["confidence"] in (p.get("model_auto_confidence") or []) and abs(amount) <= p["model_auto_max_amount"]:
        return "auto_applied", f"model, {prop['confidence']} confidence, under {p['model_auto_max_amount']:.0f}"
    return "pending", f"model, {prop['confidence']} confidence, {abs(amount):.2f}"


def match_decision(m) -> tuple[str, str]:
    return ("auto_applied" if m.status == "auto" else "pending"), m.rule.replace("_", " ") + (f"; {m.note}" if m.note else "")


# ---------------------------------------------------------------- store and audit
SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (id INTEGER PRIMARY KEY, kind TEXT, ref TEXT, title TEXT, payload TEXT, status TEXT,
  reason TEXT, decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, object TEXT, detail TEXT,
  prev_hash TEXT, hash TEXT);
"""


class TenantStore:
    def __init__(self, path: Path, tenant: str):
        self.tenant = tenant
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # audit -------------------------------------------------------------
    def _last_hash(self) -> str:
        r = self.db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return r["hash"] if r else "0" * 64

    @staticmethod
    def _digest(prev: str, ts: str, actor: str, action: str, obj: str, detail: str) -> str:
        return hashlib.sha256(json.dumps([prev, ts, actor, action, obj, detail]).encode()).hexdigest()

    def log(self, actor: str, action: str, obj: str, detail: dict | str = "", commit=True):
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        d = detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True, default=str)
        prev = self._last_hash()
        h = self._digest(prev, ts, actor, action, obj, d)
        self.db.execute("INSERT INTO audit (ts, actor, action, object, detail, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                        (ts, actor, action, obj, d, prev, h))
        if commit:
            self.db.commit()

    def verify_audit(self) -> tuple[bool, int | None]:
        prev = "0" * 64
        for r in self.db.execute("SELECT * FROM audit ORDER BY seq"):
            if r["prev_hash"] != prev or self._digest(prev, r["ts"], r["actor"], r["action"], r["object"], r["detail"]) != r["hash"]:
                return False, r["seq"]
            prev = r["hash"]
        return True, None

    # proposals ---------------------------------------------------------
    def add(self, kind, ref, title, payload, status, reason, actor):
        cur = self.db.execute("INSERT INTO proposals (kind, ref, title, payload, status, reason, decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?)",
                              (kind, ref, title, json.dumps(payload, default=str), status, reason,
                               actor if status != "pending" else None, None))
        self.log(actor, f"propose:{status}", f"{kind}:{ref}", {"reason": reason}, commit=False)
        return cur.lastrowid

    def decide(self, pid: int, status: str, user: str, note: str = ""):
        row = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(pid)
        if row["status"] not in ("pending", "blocked", "held"):
            raise ValueError(f"proposal {pid} is already {row['status']}")
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.db.execute("UPDATE proposals SET status=?, decided_by=?, decided_at=? WHERE id=?", (status, user, ts, pid))
        self.log(user, f"decide:{status}", f"{row['kind']}:{row['ref']}", {"note": note, "was": row["status"]})

    def decide_group(self, pid: int, status: str, user: str) -> int:
        """Decide a posting and every other pending posting from the same new counterparty in one step;
        an approval also becomes a memory rule for future months."""
        row = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(pid)
        group = json.loads(row["payload"]).get("group") if row["kind"] == "posting" else None
        ids = [pid]
        if group:
            ids = [r["id"] for r in self.db.execute("SELECT id, payload FROM proposals WHERE kind='posting' AND status='pending'")
                   if json.loads(r["payload"]).get("group") == group] or [pid]
        for i in ids:
            self.decide(i, status, user, note=f"group decision ({len(ids)} lines)" if len(ids) > 1 else "")
        if group and status == "approved":
            self.log(user, "learn:rule", f"counterparty:{group}", {"account_code": json.loads(row["payload"]).get("account_code")})
        return len(ids)

    def proposals(self, status: str | None = None, kind: str | None = None):
        q, args = "SELECT * FROM proposals WHERE 1=1", []
        if status:
            q += " AND status=?"; args.append(status)
        if kind:
            q += " AND kind=?"; args.append(kind)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", args)]

    def audit(self, limit=200):
        return [dict(r) for r in self.db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))]

    def commit(self):
        self.db.commit()
