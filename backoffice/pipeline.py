"""Month-end close for one company: inbox -> matching -> categorisation -> policy -> report."""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date
from pathlib import Path

from . import agents
from .connectors import load_tenant
from .control import TenantStore, email_decision, match_decision, txn_decision
from .reporting import compute_facts, render_pdf

AGENT = "agent"


def run_close(company_dir: Path, llm, var_dir: Path, today=date(2026, 11, 3), month_end=date(2026, 10, 31),
              llm_for_all: bool = False, redact: bool = True, policy_override: dict | None = None) -> dict:
    t0 = time.time()
    t = load_tenant(Path(company_dir))
    cid = t.id
    out = Path(var_dir) / cid
    out.mkdir(parents=True, exist_ok=True)
    db = out / "tenant.sqlite"
    if db.exists():
        db.unlink()
    store = TenantStore(db, cid)
    store.log("system", "close:start", f"tenant:{cid}", {"month_end": month_end.isoformat(), "model": getattr(llm, "name", "?")})
    policy = {**t.config.get("policy", {}), **(policy_override or {})}

    # 1 inbox
    emails = agents.run_inbox(t, llm, today, redact=redact)
    ap = []
    for r in emails:
        status, why = email_decision(r.category, r.flags)
        title = f"{r.category.replace('_', ' ')}: {r.subject}"
        store.add("email", r.message_id, title, r.__dict__, status, why, AGENT)
        if r.category == "supplier_invoice" and r.invoice and "duplicate" not in r.flags and r.invoice.get("total"):
            ap.append({"invoice_id": r.message_id, "party": r.invoice.get("supplier_master") or r.invoice.get("supplier") or "?",
                       "number": r.invoice.get("invoice_number") or "", "total": r.invoice["total"], "due_date": r.invoice.get("due_date"),
                       "default_account": r.invoice.get("default_account"), "flags": r.flags})
    # 2 matching
    matches = agents.reconcile(t, ap, tol=float(policy.get("match_tolerance", 15.0)))
    for m in matches.values():
        status, why = match_decision(m)
        store.add("match", m.txn_id, f"match {m.txn_id} -> {', '.join(m.invoice_ids) or 'nothing'}", m.__dict__, status, why, AGENT)
    # 3 categorisation
    inv_by_id = {a["invoice_id"]: a for a in ap}
    props, llm_all, _ = agents.categorise(t, llm, matches, inv_by_id, llm_for_all=llm_for_all)
    txn_by_id = {x.txn_id: x for x in t.bank}
    acct_name = {a.code: a.name for a in t.accounts}
    groups = Counter()
    for tid, p in props.items():
        if p["source"] == "model":
            p["group"] = agents.counterparty_key(txn_by_id[tid].description) or tid
            groups[p["group"]] += 1
    for tid, p in props.items():
        x = txn_by_id[tid]
        status, why = txn_decision(p, x.amount, policy)
        m = matches.get(tid)
        if m is not None and m.status == "review" and status == "auto_applied":
            status, why = "pending", f"bank line has an unresolved match ({m.rule.replace('_', ' ')})"
        p["status"] = status
        n = groups.get(p.get("group"), 0)
        acct = p["account_code"] if p["account_code"] not in acct_name or acct_name[p["account_code"]] == p["account_code"] \
            else f"{p['account_code']} {acct_name[p['account_code']]}"
        title = f"{x.date.isoformat()} {x.description} {x.amount:+,.2f} -> {acct}" + (f" (1 of {n} lines from this counterparty)" if n > 1 else "")
        store.add("posting", tid, title, p, status, why, AGENT)
    store.commit()
    # 4 report
    pend = Counter(r["kind"] for r in store.proposals("pending"))
    blocked = len(store.proposals("blocked"))
    facts, allowed, raw = compute_facts(t, props, matches, ap, pend, blocked, month_end)
    commentary, check = agents.write_commentary(llm, facts, allowed)
    queue = store.proposals("blocked") + store.proposals("pending") + store.proposals("held")
    render_pdf(out / f"management_report_{month_end:%Y-%m}.pdf", t, facts, raw, commentary, queue, check)
    store.add("report", f"{month_end:%Y-%m}", f"Management report {facts['period']}", {"facts": facts, "check": check}, "pending",
              "report ready for owner sign-off", AGENT)
    store.log("system", "close:end", f"tenant:{cid}", {"seconds": round(time.time() - t0, 1)})
    store.commit()

    run = {"company": cid, "model": getattr(llm, "name", "?"), "seconds": round(time.time() - t0, 1), "policy": policy,
           "emails": [r.__dict__ for r in emails], "matches": {k: v.__dict__ for k, v in matches.items()},
           "postings": props, "llm_all": llm_all, "facts": facts, "raw": raw, "commentary": commentary, "commentary_check": check,
           "status_counts": dict(Counter(r["status"] for r in store.proposals()))}
    (out / "run.json").write_text(json.dumps(run, indent=1, default=str))
    return run
