"""Run the four-account demo end to end and score it.

  python -m assistant.demo --llm hf --model <path> --out runs/assistant_demo        # GPU, open-weight model
  python -m assistant.demo --llm cache --out runs/assistant_demo                    # offline re-run from recorded responses
  python -m assistant.demo --llm openai --model <name> --out var/assistant          # any OpenAI-compatible endpoint

World: Microsoft 365, Google and Zoho accounts are served by the local API stand-ins (assistant/standins.py) through
httpx.MockTransport; the cPanel account is a real IMAP server (pymap) and a real CalDAV server (Radicale) on localhost.
"""
from __future__ import annotations

import argparse
import imaplib
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from collections import Counter
from pathlib import Path

import httpx

from backoffice.cli import make_backend, model_key
from backoffice.llm import Cached

from . import demo_data as D
from .agent import Assistant, fmt_slot, summary_counts
from .providers.gmail import GmailMailbox
from .providers.graph import GraphMailbox
from .providers.imap_caldav import ImapCalDavMailbox
from .providers.zoho import ZohoMailbox
from .standins import GoogleStandIn, GraphStandIn, ZohoStandIn

PASSWORD = "demo-only-password"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"port {port} did not open")


class World:
    """Four accounts on four providers, seeded with the synthetic mail, sent items and calendars."""

    def __init__(self, workdir: Path):
        self.procs = []
        self.workdir = workdir
        self.standins = {}
        bindir = Path(sys.executable).parent
        # ---------------- cPanel-style account: real IMAP + CalDAV servers
        acc = next(a for a in D.ACCOUNTS if a["provider"] == "imap")
        self.imap_port, self.dav_port = free_port(), free_port()
        self.procs.append(subprocess.Popen([str(bindir / "pymap"), "--no-tls", "--host", "127.0.0.1", "--port", str(self.imap_port),
                                            "dict", "--demo-user", acc["address"], "--demo-password", PASSWORD],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        htp = workdir / "htpasswd"
        htp.write_text(f"{acc['address']}:{PASSWORD}\n")
        self.procs.append(subprocess.Popen([sys.executable, "-m", "radicale", "--server-hosts", f"127.0.0.1:{self.dav_port}",
                                            "--auth-type", "htpasswd", "--auth-htpasswd-filename", str(htp),
                                            "--auth-htpasswd-encryption", "plain", "--storage-filesystem-folder", str(workdir / "dav"),
                                            "--rights-type", "owner_only"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_port(self.imap_port)
        wait_port(self.dav_port)
        c = imaplib.IMAP4("127.0.0.1", self.imap_port)
        c.login(acc["address"], PASSWORD)
        for f in ("Sent", "Drafts"):
            c.create(f)
        for raw, m in D.messages_for(acc["key"]):
            c.append("INBOX", None, imaplib.Time2Internaldate(m["date"].timestamp()), raw)
        for n, s in enumerate(D.SENT[acc["key"]]):
            c.append("Sent", "(\\Seen)", None, D.sent_rfc822(acc, s, n))
        c.logout()
        import caldav
        cal = caldav.DAVClient(url=f"http://127.0.0.1:{self.dav_port}/", username=acc["address"], password=PASSWORD).principal().make_calendar(name="Calendar")
        for s, e, title, loc in D.EVENTS[acc["key"]]:
            cal.save_event(dtstart=s, dtend=e, summary=title, location=loc)
        # ---------------- API accounts behind stand-ins
        for acc in D.ACCOUNTS:
            if acc["provider"] == "imap":
                continue
            si = {"m365": lambda: GraphStandIn(acc["address"]), "google": lambda: GoogleStandIn(acc["address"]),
                  "zoho": lambda: ZohoStandIn("6000000012345", "a1b2c3d4e5")}[acc["provider"]]()
            inbox_f, sent_f = {"m365": ("inbox", "sentitems"), "google": ("INBOX", "SENT"), "zoho": ("Inbox", "Sent")}[acc["provider"]]
            for raw, _ in D.messages_for(acc["key"]):
                si.load(inbox_f, raw)
            for n, s in enumerate(D.SENT[acc["key"]]):
                si.load(sent_f, D.sent_rfc822(acc, s, n))
            for s, e, title, loc in D.EVENTS[acc["key"]]:
                si.add_event(s, e, title, loc)
            self.standins[acc["key"]] = si

    def mailboxes(self):
        tok = lambda: "demo-token"  # noqa: E731
        out = {}
        for acc in D.ACCOUNTS:
            k, p = acc["key"], acc["provider"]
            if p == "imap":
                out[k] = ImapCalDavMailbox(k, acc["address"], D.OWNER, PASSWORD, "127.0.0.1", self.imap_port,
                                           caldav_url=f"http://127.0.0.1:{self.dav_port}/", ssl=False)
                continue
            client = httpx.Client(transport=httpx.MockTransport(self.standins[k]))
            if p == "m365":
                out[k] = GraphMailbox(k, acc["address"], D.OWNER, tok, client=client)
            elif p == "google":
                out[k] = GmailMailbox(k, acc["address"], D.OWNER, tok, client=client)
            else:
                out[k] = ZohoMailbox(k, acc["address"], D.OWNER, tok, "6000000012345", "a1b2c3d4e5", client=client)
        return out

    def close(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            p.wait(10)


# ------------------------------------------------------------------ scoring
def score(asst: Assistant, readback: dict) -> dict:
    truth = {(m["account"], m["subject"]): m["truth"] for m in D.INBOX}
    rows, ok = [], Counter()
    for it in asst.items:
        tr = truth[(it.msg.account, it.msg.subject)]
        r = {"account": it.msg.account, "subject": it.msg.subject, "expected": tr["category"], "got": it.category,
             "model_category": it.triage.get("category"), "signals": it.signals}
        if tr["category"] == "meeting" and it.meeting:
            r["time_ok"] = it.meeting["start"] == tr["start"] and (it.meeting["end"] - it.meeting["start"]) == timedelta(minutes=tr["minutes"])
            r["clash_ok"] = bool(it.meeting["clash"]) == tr["conflict"]
        if tr["category"] == "travel":
            r["legs_ok"] = [(l["start"], l["end"]) for l in it.legs] == tr["legs"]
        rows.append(r)
    n = len(rows)
    risk = [r for r in rows if r["expected"] == "risk"]
    meet = [r for r in rows if r["expected"] == "meeting"]
    trav = [r for r in rows if r["expected"] == "travel"]
    return {
        "emails": n,
        "category_correct": sum(r["expected"] == r["got"] for r in rows),
        "model_category_correct": sum(r["expected"] == r["model_category"] for r in rows),
        "risk_caught": sum(r["got"] == "risk" for r in risk), "risk_total": len(risk),
        "risk_caught_by_checks": sum(bool(r["signals"]) for r in risk),
        "false_risk": sum(r["got"] == "risk" and r["expected"] != "risk" for r in rows),
        "risk_drafts": sum(1 for it in asst.items if it.category == "risk" and it.draft),
        "meetings": len(meet), "meeting_time_correct": sum(bool(r.get("time_ok")) for r in meet),
        "meeting_clash_correct": sum(bool(r.get("clash_ok")) for r in meet),
        "travel": len(trav), "travel_legs_correct": sum(bool(r.get("legs_ok")) for r in trav),
        "drafts_written": sum(1 for it in asst.items if it.draft),
        "drafts_found_in_accounts": sum(len(v["drafts"]) for v in readback.values()),
        "holds_written": len(asst.holds),
        "handoffs": len(asst.handoffs),
        "confusions": [r for r in rows if r["expected"] != r["got"]],
    }


SIGNOFF = {"harbourlane": "Sam Doyle | Director", "bluegum": None, "saltbush": None, "doyle": None}
GENERIC = re.compile(r"I hope this (e-?mail|message) finds you well|please (do not|don't) hesitate|thank you for reaching out|"
                     r"best regards|kind regards|warm regards|sincerely|I trust this|\[Your Name\]|Dear ", re.I)


def style(text: str) -> dict:
    """Words, stock phrases, and whether the email ends the way Sam ends emails ("..., Sam" or "S"; the Harbourlane
    signature line is set aside first)."""
    core = re.sub(r"(?m)^\s*Sam Doyle \|.*$", "", text).strip()
    return {"words": len(core.split()), "generic_phrases": len(GENERIC.findall(text)),
            "signed_sam": bool(re.search(r"\b(Sam|S)[.!]?$", core))}


def style_report(asst: Assistant) -> dict:
    import statistics as st
    sent = [s.body for v in asst.sent.values() for s in v]
    drafts = [i.draft for i in asst.items if i.draft]
    base = [i.baseline for i in asst.items if i.baseline]
    def agg(xs):
        if not xs:
            return {}
        ss = [style(x) for x in xs]
        w = sorted(s["words"] for s in ss)
        return {"n": len(xs), "median_words": st.median(w), "range_words": [w[0], w[-1]],
                "with_generic_phrases": sum(s["generic_phrases"] > 0 for s in ss), "signed_like_sam": sum(s["signed_sam"] for s in ss)}
    return {"sam_sent": agg(sent), "drafts_with_examples": agg(drafts), "drafts_without_examples": agg(base)}


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="cache", choices=["hf", "openai", "cache", "fake"])
    ap.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--out", default="runs/assistant_demo")
    ap.add_argument("--no-baseline", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    backend = make_backend(a.llm, a.model)
    llm = Cached(backend, out / "llm_cache.jsonl", offline=a.llm == "cache")
    llm.name = model_key(a.model)
    with tempfile.TemporaryDirectory() as tmp:
        world = World(Path(tmp))
        try:
            mbs = world.mailboxes()
            asst = Assistant(mbs, {x["key"]: x["business"] for x in D.ACCOUNTS}, llm, D.NOW, D.SINCE, D.KNOWN, D.INTERNAL_PEOPLE,
                             {x["key"]: x["bookkeeping"] for x in D.ACCOUNTS}, D.OWNER, baseline=not a.no_baseline).run()
            # read everything back through the same adapters: what would the owner see in each account?
            readback = {}
            for k, mb in mbs.items():
                evs = mb.events(D.NOW, D.NOW + timedelta(days=21))
                readback[k] = {"provider": mb.provider, "address": mb.address, "drafts": mb.drafts(),
                               "events": [e.to_dict() for e in sorted(evs, key=lambda e: e.start)]}
            calls = {k: len(si.calls) for k, si in world.standins.items()}
        finally:
            world.close()
    sc = score(asst, readback)
    sc["style"] = style_report(asst)
    sc["llm"] = {"model": llm.name, "cache_hits": llm.hits, "cache_misses": llm.misses}
    sc["api_calls_to_standins"] = calls
    (out / "items.json").write_text(json.dumps([i.to_dict() for i in asst.items], indent=1, default=str))
    (out / "readback.json").write_text(json.dumps(readback, indent=1, default=str))
    (out / "handoffs.json").write_text(json.dumps(asst.handoffs, indent=1))
    (out / "holds.json").write_text(json.dumps(asst.holds, indent=1))
    (out / "score.json").write_text(json.dumps(sc, indent=1, default=str))
    print(json.dumps({k: v for k, v in sc.items() if k != "confusions"}, indent=1, default=str))
    for c in sc["confusions"]:
        print("CONFUSION", c)


if __name__ == "__main__":
    main()
