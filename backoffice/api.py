"""Approval web app: portfolio view, per-company decision queue, audit trail.

  BACKOFFICE_VAR=var BACKOFFICE_SECRET=... uvicorn backoffice.api:app --port 8080

Access is scoped by role and company: a company bookkeeper sees one company; group finance sees all;
an auditor can read everything and change nothing. Every decision is written to the tenant's audit chain.
The demo users below stand in for single sign-on (Microsoft Entra ID or Google Workspace) in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from jinja2 import Environment

from .control import TenantStore

VAR = Path(os.environ.get("BACKOFFICE_VAR", "var"))
DATA = Path(os.environ.get("BACKOFFICE_DATA", "data/companies"))
SECRET = os.environ.get("BACKOFFICE_SECRET", "change-me").encode()
USERS = {
    "maria": {"name": "Maria (group finance lead)", "role": "approver", "tenants": "*"},
    "sean": {"name": "Sean (bookkeeper, Tarrowin IT)", "role": "approver", "tenants": ["C04"]},
    "audit": {"name": "External auditor (read only)", "role": "auditor", "tenants": "*"},
}

app = FastAPI(title="Back-office agents")
env = Environment(autoescape=True)


def _sign(u: str) -> str:
    return u + "." + hmac.new(SECRET, u.encode(), hashlib.sha256).hexdigest()[:32]


def current_user(req: Request) -> tuple[str, dict]:
    tok = req.cookies.get("session", "")
    u = tok.rsplit(".", 1)[0]
    if u in USERS and hmac.compare_digest(_sign(u), tok):
        return u, USERS[u]
    raise HTTPException(401)


def tenants() -> list[dict]:
    out = []
    for d in sorted(DATA.glob("C*")):
        cfg = yaml.safe_load((d / "company.yaml").read_text())
        run = VAR / cfg["id"] / "run.json"
        out.append({"cfg": cfg, "run": json.loads(run.read_text()) if run.exists() else None})
    return out


def allowed(user: dict, cid: str) -> bool:
    return user["tenants"] == "*" or cid in user["tenants"]


def store_for(user: dict, cid: str) -> TenantStore:
    if not allowed(user, cid):
        raise HTTPException(403, "not your company")
    db = VAR / cid / "tenant.sqlite"
    if not db.exists():
        raise HTTPException(404)
    return TenantStore(db, cid)


CSS = """
:root{--ink:#1d2433;--muted:#5b6475;--line:#dfe3ea;--bg:#f6f7f9;--card:#fff;--accent:#0b5cad;--warn:#a15c00;--bad:#b42318;--good:#1a7f37}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
header{background:#14213d;color:#fff;padding:12px 24px;display:flex;justify-content:space-between;align-items:center}
header a{color:#fff;text-decoration:none;font-weight:600}header span{opacity:.85;font-size:13px}
main{max-width:1240px;margin:0 auto;padding:20px 24px}h1{font-size:20px;margin:4px 0 2px}h2{font-size:15px;margin:22px 0 8px}
.sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}.tile b{font-size:20px;display:block}
.tile small{color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{background:#eef1f5;font-size:12px;color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.blocked{background:#fde7e7;color:var(--bad)}.pending{background:#fff3dc;color:var(--warn)}.held{background:#eceff3;color:#444}
.auto_applied,.approved{background:#e3f4e8;color:var(--good)}.rejected{background:#eceff3;color:#444}
button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}
button.ok{border-color:var(--good);color:var(--good)}button.no{border-color:var(--bad);color:var(--bad)}
.why{color:var(--muted);font-size:12px}.chain{padding:8px 12px;border-radius:8px;display:inline-block;font-weight:600}
.chain.ok{background:#e3f4e8;color:var(--good)}.chain.bad{background:#fde7e7;color:var(--bad)}
.bar{height:8px;background:#e9edf2;border-radius:4px;overflow:hidden;min-width:90px}.bar i{display:block;height:100%;background:var(--accent)}
a{color:var(--accent)}code{font-size:12px}
.login{max-width:420px;margin:60px auto;background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px}
.login button{display:block;width:100%;margin:8px 0;padding:10px;font-size:14px;text-align:left}
"""

BASE = """<!doctype html><html><head><meta charset=utf-8><title>{{ title }}</title><style>""" + CSS + """</style></head><body>
<header><a href="/">Back-office agents</a>{% if user %}<span>{{ user.name }} · <a href="/logout" style="font-weight:400">sign out</a></span>{% endif %}</header>
<main>{{ body|safe }}</main></body></html>"""

PORTFOLIO = """<h1>Portfolio: October 2026 close</h1><div class=sub>{{ rows|length }} companies visible to you · figures from the agents' close runs</div>
<div class=tiles>
<div class=tile><small>Items processed</small><b>{{ tot.n }}</b><small>emails, bank lines, matches</small></div>
<div class=tile><small>Applied automatically</small><b>{{ '%.0f'|format(100*tot.auto/tot.n) }}%</b><small>by memory, matching or policy</small></div>
<div class=tile><small>Waiting for a person</small><b>{{ tot.pending }}</b><small>across all companies</small></div>
<div class=tile><small>High-risk items blocked</small><b>{{ tot.blocked }}</b><small>bank changes, spoofing, injection</small></div></div>
<table><tr><th>Company</th><th>Stack (bank / ledger)</th><th>Automated</th><th class=num>Waiting</th><th class=num>Blocked</th>
<th class=num>Net cash flow</th><th>Report</th></tr>
{% for r in rows %}<tr><td><a href="/c/{{ r.id }}">{{ r.name }}</a><div class=why>{{ r.sector }}</div></td><td><code>{{ r.stack }}</code></td>
<td><div class=bar><i style="width:{{ r.auto_pct }}%"></i></div><span class=why>{{ r.auto_pct }}%</span></td>
<td class=num>{{ r.pending }}</td><td class=num>{{ r.blocked }}</td><td class=num>{{ r.cash }}</td>
<td><a href="/c/{{ r.id }}/report">PDF</a></td></tr>{% endfor %}</table>"""

COMPANY = """<h1>{{ cfg.name }}</h1><div class=sub>{{ cfg.sector }} · {{ cfg.currency }} · bank format {{ cfg.stack.bank_format }},
ledger export {{ cfg.stack.ledger_format }} · <a href="/c/{{ cfg.id }}/report">management report (PDF)</a></div>
<h2>Decisions needed ({{ queue|length }})</h2>
<table><tr><th>Status</th><th>Item</th><th>Why it is here</th><th></th></tr>
{% for p in queue %}<tr><td><span class="pill {{ p.status }}">{{ p.status }}</span></td><td>{{ p.title }}</td><td class=why>{{ p.reason }}
{% if p.notes %}<br>{{ p.notes }}{% endif %}</td>
<td style="white-space:nowrap">{% if can_decide %}<form method=post action="/c/{{ cfg.id }}/decide/{{ p.id }}" style="display:inline">
<button class=ok name=action value=approved>Approve</button> <button class=no name=action value=rejected>Reject</button></form>
{% else %}<span class=why>read only</span>{% endif %}</td></tr>{% endfor %}</table>
<h2>Recently decided</h2><table><tr><th>Status</th><th>Item</th><th>By</th><th>When</th></tr>
{% for p in decided %}<tr><td><span class="pill {{ p.status }}">{{ p.status }}</span></td><td>{{ p.title }}</td><td>{{ p.decided_by }}</td><td>{{ p.decided_at }}</td></tr>{% endfor %}
</table>
<h2>Audit trail</h2><div class="chain {{ 'ok' if chain_ok else 'bad' }}">{{ 'Hash chain verified: no entry altered or removed' if chain_ok else 'Chain broken at entry %s'|format(bad) }}</div>
<table style="margin-top:8px"><tr><th>#</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Object</th><th>Hash</th></tr>
{% for a in audit %}<tr><td>{{ a.seq }}</td><td>{{ a.ts }}</td><td>{{ a.actor }}</td><td>{{ a.action }}</td><td><code>{{ a.object }}</code></td>
<td><code>{{ a.hash[:12] }}</code></td></tr>{% endfor %}</table>"""

LOGIN = """<div class=login><h1>Sign in</h1><div class=sub>Demo accounts (single sign-on in production)</div>
<form method=post action="/login">{% for k, u in users.items() %}<button name=user value="{{ k }}">{{ u.name }}</button>{% endfor %}</form></div>"""


def page(title, body, user=None):
    return HTMLResponse(env.from_string(BASE).render(title=title, body=body, user=user))


@app.get("/login", response_class=HTMLResponse)
def login_form():
    return page("Sign in", env.from_string(LOGIN).render(users=USERS))


@app.post("/login")
def login(user: str = Form(...)):
    if user not in USERS:
        raise HTTPException(401)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("session", _sign(user), httponly=True, samesite="strict")
    return r


@app.get("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie("session")
    return r


@app.exception_handler(401)
def _unauth(req, exc):
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def portfolio(req: Request):
    uid, user = current_user(req)
    rows, tot = [], {"n": 0, "auto": 0, "pending": 0, "blocked": 0}
    for t in tenants():
        cfg, run = t["cfg"], t["run"]
        if not allowed(user, cfg["id"]) or not run:
            continue
        s = TenantStore(VAR / cfg["id"] / "tenant.sqlite", cfg["id"])
        props = s.proposals()
        n = len([p for p in props if p["kind"] != "report"])
        auto = sum(p["status"] == "auto_applied" for p in props)
        pend = sum(p["status"] in ("pending", "held") for p in props if p["kind"] != "report")
        blk = sum(p["status"] == "blocked" for p in props)
        tot["n"] += n; tot["auto"] += auto; tot["pending"] += pend; tot["blocked"] += blk
        rows.append({"id": cfg["id"], "name": cfg["name"], "sector": cfg["sector"], "stack": f"{cfg['stack']['bank_format']} / {cfg['stack']['ledger_format']}",
                     "auto_pct": round(100 * auto / n) if n else 0, "pending": pend, "blocked": blk,
                     "cash": f"{cfg['currency']} {run['raw']['net_cash_flow']:,.0f}"})
    if not tot["n"]:
        tot["n"] = 1
    return page("Portfolio", env.from_string(PORTFOLIO).render(rows=rows, tot=tot), user)


@app.get("/c/{cid}", response_class=HTMLResponse)
def company(cid: str, req: Request):
    uid, user = current_user(req)
    s = store_for(user, cid)
    cfg = yaml.safe_load((DATA / cid / "company.yaml").read_text())
    order = {"blocked": 0, "pending": 1, "held": 2}
    queue = sorted([p for p in s.proposals() if p["status"] in order], key=lambda p: (order[p["status"]], p["id"]))
    for p in queue:
        pl = json.loads(p["payload"])
        p["notes"] = "; ".join(pl.get("notes", [])) if isinstance(pl, dict) else ""
    decided = [p for p in s.proposals() if p["status"] in ("approved", "rejected")][-10:]
    ok, bad = s.verify_audit()
    body = env.from_string(COMPANY).render(cfg=cfg, queue=queue, decided=decided, audit=s.audit(25), chain_ok=ok, bad=bad,
                                           can_decide=user["role"] == "approver")
    return page(cfg["name"], body, user)


@app.post("/c/{cid}/decide/{pid}")
def decide(cid: str, pid: int, req: Request, action: str = Form(...)):
    uid, user = current_user(req)
    if user["role"] != "approver":
        raise HTTPException(403, "read-only role")
    if action not in ("approved", "rejected"):
        raise HTTPException(400)
    s = store_for(user, cid)
    try:
        s.decide_group(pid, action, uid)
    except (KeyError, ValueError) as e:
        raise HTTPException(409, str(e))
    return RedirectResponse(f"/c/{cid}", status_code=303)


@app.get("/c/{cid}/report")
def report(cid: str, req: Request):
    uid, user = current_user(req)
    store_for(user, cid)
    pdfs = sorted((VAR / cid).glob("management_report_*.pdf"))
    if not pdfs:
        raise HTTPException(404)
    return FileResponse(pdfs[-1], media_type="application/pdf")
