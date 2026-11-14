"""Render the morning brief (what the owner sees at 7 am) and the results page from a demo run directory.

  python -m assistant.brief runs/assistant_demo            -> runs/assistant_demo/brief.html
"""
from __future__ import annotations

import html
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import demo_data as D

COL = {"harbourlane": "#0072B2", "bluegum": "#009E73", "saltbush": "#D55E00", "doyle": "#CC79A7"}
PROV = {"m365": "Microsoft 365", "google": "Google Workspace", "zoho": "Zoho Mail (AU)", "imap": "cPanel (IMAP + CalDAV)"}
HOW = {
    "m365": ("Microsoft Graph API", "App registration limited to Sam's mailbox by Exchange RBAC for Applications. Reads mail and calendar; "
             "writes drafts, tentative holds and categories."),
    "google": ("Gmail and Calendar APIs", "Internal OAuth app in the company's own Workspace, approved by its admin. Reads mail and calendar; "
               "writes drafts, tentative holds and labels."),
    "zoho": ("Zoho Mail and Calendar APIs", "OAuth on Zoho's Australian data centre (zoho.com.au). Reads mail and calendar; writes drafts "
             "and holds."),
    "imap": ("IMAP + CalDAV (open standards)", "The mailbox's own login over TLS (IMAP 993, CalDAV 2080 on cPanel). Reads mail and calendar; "
             "writes drafts, tentative holds and keywords."),
}
LABEL = {"accounts": "To bookkeeping", "meeting": "Calendar", "travel": "Travel", "reply": "Reply drafted", "fyi": "FYI", "risk": "Check first"}

CSS = """
:root{--ink:#1d2430;--muted:#5b6575;--line:#dde2ea;--bg:#ffffff;--soft:#f5f7fa;--warn:#b42318;--warnbg:#fdf0ef}
*{box-sizing:border-box}body{margin:0;background:#e9edf2;font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;color:var(--ink);font-size:13px;line-height:1.4}
.page{width:1600px;margin:0 auto 24px;background:var(--bg);padding:34px 40px;page-break-after:always}
h1{font-size:26px;margin:0 0 2px}h2{font-size:15px;margin:0 0 10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.sub{color:var(--muted);font-size:14px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}
.card{border:1px solid var(--line);border-top:5px solid;border-radius:8px;padding:12px 14px}
.card .p{font-weight:700;font-size:14px}.card .a{color:var(--muted);font-size:12px;margin-bottom:8px}
.nums{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;text-align:center}.nums b{display:block;font-size:20px}.nums span{font-size:10.5px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1.15fr 1fr;gap:26px}
.box{border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:18px}
.risk{background:var(--warnbg);border-color:#f3c3be}.risk li{margin-bottom:7px}.risk b{color:var(--warn)}
ul{margin:0;padding-left:18px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
.day{margin-bottom:9px}.day .d{font-weight:700;margin-bottom:3px}
.ev{display:flex;gap:8px;align-items:baseline;padding:2px 0}.ev .t{width:150px;flex:none;white-space:nowrap;color:var(--muted);font-variant-numeric:tabular-nums}
.badge{font-size:10.5px;font-weight:700;border-radius:4px;padding:1px 6px;margin-left:6px}
.new{background:#e7f3ff;color:#0b5394}.clash{background:var(--warnbg);color:var(--warn)}.away{background:#fff4e0;color:#8a5300}
.draft{display:grid;grid-template-columns:1fr 1fr;gap:12px;border-top:1px solid var(--line);padding-top:10px;margin-top:10px}
.draft:first-of-type{border-top:0;margin-top:0;padding-top:0}
.q{background:var(--soft);border-radius:6px;padding:8px 10px;white-space:pre-wrap;font-size:12px}
.r{border:1px dashed #9db4cc;border-radius:6px;padding:8px 10px;white-space:pre-wrap;font-size:12px}
.lbl{font-size:11px;color:var(--muted);margin-bottom:3px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted)}
.small{font-size:12px;color:var(--muted)}.foot{margin-top:14px;font-size:11.5px;color:var(--muted)}
.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}.kpi div{background:var(--soft);border-radius:8px;padding:12px}
.kpi b{font-size:24px;display:block}
"""


def esc(s):
    return html.escape(str(s))


def hm(d: datetime) -> str:
    return d.strftime("%I:%M %p").lstrip("0").lower()


def build(run: Path) -> str:
    items = json.loads((run / "items.json").read_text())
    rb = json.loads((run / "readback.json").read_text())
    sc = json.loads((run / "score.json").read_text())
    holds = json.loads((run / "holds.json").read_text())
    acc = {a["key"]: a for a in D.ACCOUNTS}
    tz = D.TZ
    # ---------------- cards
    cards = []
    for k, a in acc.items():
        its = [i for i in items if i["msg"]["account"] == k]
        n = lambda c: sum(i["category"] == c for i in its)  # noqa: E731
        nd = sum(1 for i in its if i["draft"])
        nh = sum(1 for h in holds if h["account"] == k)
        cards.append(f'<div class="card" style="border-top-color:{COL[k]}"><div class="p">{esc(a["business"].split(" (")[0])}</div>'
                     f'<div class="a">{esc(PROV[a["provider"]])} &middot; {esc(a["address"])}</div><div class="nums">'
                     f'<div><b>{len(its)}</b><span>new</span></div><div><b>{nd}</b><span>drafts</span></div><div><b>{nh}</b><span>holds</span></div>'
                     f'<div><b>{n("accounts")}</b><span>to books</span></div><div><b>{n("risk")}</b><span>check first</span></div></div></div>')
    # ---------------- risk
    risk = [i for i in items if i["category"] == "risk"]
    rl = "".join(f'<li><span class="dot" style="background:{COL[i["msg"]["account"]]}"></span><b>{esc(i["msg"]["subject"])}</b> '
                 f'from {esc(i["msg"]["sender_name"])} &lt;{esc(i["msg"]["sender"])}&gt;<br><span class="small">'
                 f'{esc("; ".join(i["signals"]) or "; ".join(i["notes"]))}. No reply drafted, nothing actioned.</span></li>' for i in risk)
    # ---------------- calendar: merged across all four accounts, Mon-Sun of this week
    evs = []
    for k, v in rb.items():
        for e in v["events"]:
            s, f = datetime.fromisoformat(e["start"]).astimezone(tz), datetime.fromisoformat(e["end"]).astimezone(tz)
            evs.append((s, f, k, e["title"], e["tentative"]))
    week_end = (D.NOW + timedelta(days=7 - D.NOW.weekday())).replace(hour=0, minute=0)
    by_day = defaultdict(list)
    for s, f, k, t, tent in sorted(evs):
        if s < week_end:
            by_day[s.date()].append((s, f, k, t, tent))
    away, windows = {}, []
    for i in items:
        if i["category"] == "travel" and i["legs"]:
            a0 = min(datetime.fromisoformat(l["start"]) for l in i["legs"]).astimezone(tz)
            a1 = max(datetime.fromisoformat(l["end"]) for l in i["legs"]).astimezone(tz)
            away[a0.date()] = i["msg"]["subject"].split(": ")[-1]
            windows.append((a0, a1, {l["label"] for l in i["legs"]}))
    clashes = [i for i in items if i.get("meeting") and i["meeting"]["clash"]]
    cal = []
    day = D.NOW.date()
    while day < week_end.date():
        rows = by_day.get(day, [])
        dl = f'<div class="d">{day.strftime("%A %d %B")}' + (f'<span class="badge away">away: {esc(away[day])}</span>' if day in away else "") + "</div>"
        lines = []
        for s, f, k, t, tent in rows:
            badge = '<span class="badge new">new hold</span>' if tent else ""
            note = ""
            # an existing (not newly held) event inside a day trip, other than the flights themselves
            if not tent and any(a0 < f and s < a1 and t not in labels for a0, a1, labels in windows):
                note = '<span class="badge clash">clashes with trip</span>'
            lines.append((s, f'<div class="ev"><span class="t">{hm(s)} to {hm(f)}</span><span><span class="dot" style="background:{COL[k]}"></span>{esc(t)}{badge}{note}</span></div>'))
        for i in clashes:
            ms = datetime.fromisoformat(i["meeting"]["start"]).astimezone(tz)
            if ms.date() == day:
                alts = ", ".join(datetime.fromisoformat(x).astimezone(tz).strftime("%a %d %b ") + hm(datetime.fromisoformat(x).astimezone(tz))
                                 for x in i["meeting"]["alternatives"])
                lines.append((ms, f'<div class="ev"><span class="t">{hm(ms)}</span><span><span class="dot" style="background:{COL[i["msg"]["account"]]}"></span>'
                         f'{esc(i["msg"]["sender_name"])} asked for this time<span class="badge clash">clash: {esc(i["meeting"]["clash"][0].split(" (")[0])}</span>'
                         f'<br><span class="small">Not booked. Reply drafted offering {esc(alts)}.</span></span></div>'))
        body = "".join(h for _, h in sorted(lines, key=lambda x: x[0]))
        if body or day.weekday() < 5:
            cal.append(f'<div class="day">{dl}{body or "<span class=small>Nothing booked</span>"}</div>')
        day += timedelta(days=1)
    # ---------------- example drafts
    pick = ["Unit 4 asking rent", "Meeting re Unit 5 lease dispute", "Extra casual for December", "Catering order was wrong"]
    dr = ""
    for subj in pick:
        it = next((i for i in items if i["msg"]["subject"] == subj and i["draft"]), None)
        if not it:
            continue
        k = it["msg"]["account"]
        dr += (f'<div class="draft"><div><div class="lbl"><span class="dot" style="background:{COL[k]}"></span>{esc(PROV[acc[k]["provider"]])}: '
               f'{esc(it["msg"]["sender_name"])} wrote</div><div class="q">{esc(it["msg"]["body"][:420])}</div></div>'
               f'<div><div class="lbl">Draft waiting in Sam\'s Drafts folder</div><div class="r">{esc(it["draft"])}</div></div></div>')
    books = [i for i in items if i["category"] == "accounts"]
    bl = "".join(f'<li><span class="dot" style="background:{COL[i["msg"]["account"]]}"></span>{esc(i["msg"]["subject"])} '
                 f'<span class="small">({esc(i["msg"]["sender_name"])})</span></li>' for i in books)
    fyi = [i for i in items if i["category"] == "fyi"]
    fl = ", ".join(esc(i["msg"]["subject"]) for i in fyi)
    total = len(items)
    page1 = f"""<div class="page" style="min-height:1560px">
<h1>Good morning, Sam</h1>
<div class="sub">{D.NOW.strftime('%A %d %B, 7:00 am')} &middot; {total} new emails across 4 accounts since Friday 5 pm &middot;
{sc['drafts_written']} replies drafted &middot; {sc['holds_written']} calendar holds &middot; {sc['handoffs']} items sent to bookkeeping &middot;
{sc['risk_caught']} held for you to check &middot; nothing was sent</div>
<div class="cards">{''.join(cards)}</div>
<div class="grid"><div>
<div class="box risk"><h2>Check first: held, no reply drafted</h2><ul>{rl}</ul></div>
<div class="box"><h2>Replies waiting in your Drafts folders (4 of {sc['drafts_written']})</h2>{dr}
<div class="small" style="margin-top:10px">You read and send every draft. In this run, 3 of the {sc['drafts_written']} drafts stated an opinion or fact you had not given
(for example "the lobby layout looks good"), which is why the assistant never sends anything itself.</div></div>
</div><div>
<div class="box"><h2>This week: all four calendars</h2>{''.join(cal)}</div>
<div class="box"><h2>Sent to bookkeeping ({len(books)})</h2><ul>{bl}</ul></div>
<div class="box"><h2>FYI, filed ({len(fyi)})</h2><span class="small">{fl}</span></div>
</div></div></div>"""
    # ---------------- page 2: how it connects and what was measured
    rows = "".join(f'<tr><td><span class="dot" style="background:{COL[a["key"]]}"></span><b>{esc(PROV[a["provider"]])}</b><br>'
                   f'<span class="small">{esc(a["business"].split(" (")[0])}</span></td><td>{esc(HOW[a["provider"]][0])}</td>'
                   f'<td>{esc(HOW[a["provider"]][1])}</td></tr>' for a in D.ACCOUNTS)
    st = sc["style"]
    sw, dw, bw = st["sam_sent"], st["drafts_with_examples"], st.get("drafts_without_examples") or {}
    tone = (f'<table><tr><th></th><th>Median words</th><th>Range</th><th>Stock phrases*</th><th>Signed like Sam</th></tr>'
            f'<tr><td>Sam\'s own sent emails</td><td>{sw["median_words"]}</td><td>{sw["range_words"][0]} to {sw["range_words"][1]}</td><td>{sw["with_generic_phrases"]} of {sw["n"]}</td><td>{sw["signed_like_sam"]} of {sw["n"]}</td></tr>'
            f'<tr><td><b>Drafts using Sam\'s sent mail as examples</b></td><td>{dw["median_words"]}</td><td>{dw["range_words"][0]} to {dw["range_words"][1]}</td><td>{dw["with_generic_phrases"]} of {dw["n"]}</td><td>{dw["signed_like_sam"]} of {dw["n"]}</td></tr>')
    if bw:
        tone += (f'<tr><td>Same model, no examples</td><td>{bw["median_words"]}</td><td>{bw["range_words"][0]} to {bw["range_words"][1]}</td>'
                 f'<td>{bw["with_generic_phrases"]} of {bw["n"]}</td><td>{bw["signed_like_sam"]} of {bw["n"]}</td></tr>')
    tone += "</table>"
    base_ex = ""
    it = next((i for i in items if i["msg"]["subject"] == "Unit 4 asking rent" and i["baseline"]), None)
    if it:
        base_ex = (f'<div class="draft"><div><div class="lbl">Without examples</div><div class="q">{esc(it["baseline"])}</div></div>'
                   f'<div><div class="lbl">With Sam\'s sent mail as examples</div><div class="r">{esc(it["draft"])}</div></div></div>')
    page2 = f"""<div class="page">
<h1>How it works</h1>
<div class="sub">One assistant, one set of rules, four different email systems. Each system has its own connector; everything after the connector is shared.</div>
<div class="kpi">
<div><b>{sc['category_correct']}/{sc['emails']}</b>emails sorted correctly</div>
<div><b>{sc['risk_caught']}/{sc['risk_total']}</b>fraud and manipulation attempts held ({sc['false_risk']} false alarms)</div>
<div><b>{sc['meeting_time_correct']}/{sc['meetings']}</b>meeting times read correctly; {sc['meeting_clash_correct']}/{sc['meetings']} clashes called correctly</div>
<div><b>{sc['drafts_found_in_accounts']}/{sc['drafts_written']}</b>drafts found in the right Drafts folder when read back</div>
</div>
<div class="grid"><div>
<div class="box"><h2>How each account is connected</h2><table><tr><th>System</th><th>Connection</th><th>Access</th></tr>{rows}</table></div>
<div class="box"><h2>Ground rules</h2><ul>
<li>The assistant never sends, accepts or pays anything. Replies wait in the Drafts folder; meetings go in as tentative holds.</li>
<li>Clashes are checked across all four calendars at once, in code, not by the model.</li>
<li>Bank details, phone numbers and email addresses are masked before the model sees any text.</li>
<li>Fraud checks run before the model and do not depend on it: lookalike domains, impersonated colleagues, fake ATO links, bank-detail changes, hidden instructions.</li>
<li>Your tone comes from examples of your own sent mail, picked fresh for every draft. No model is trained on your email.</li>
</ul></div>
</div><div>
<div class="box"><h2>Tone: same model, with and without Sam's examples</h2>{tone}
<div class="small" style="margin:6px 0 12px">Example below: without examples the model also made the rent decision itself. *Stock phrases: "Dear", "Kind regards", "Please don't hesitate", "I hope this email finds you well" and similar. Sam never uses them.</div>{base_ex}</div>
</div></div>
<div class="foot">Demo on invented data: 4 accounts, 35 inbound emails, 24 sent emails, 8 calendar events; all names and domains are fictional.
The cPanel account ran on real IMAP and CalDAV servers. The Microsoft 365, Google and Zoho accounts ran the same adapter code against local stand-ins
that follow each provider's documented API. The model was an open-weight model ({esc(sc['llm']['model'].replace('models--', '').replace('--', '/'))}) on one GPU, so no email left the server.
This is the second run. The first run sorted 27 of 35 emails correctly and read 3 of 6 meeting times; it showed the model filing anything that
mentioned money as bookkeeping, inventing a date for "Wednesday", and drafting opinions. The fixes were general rules (dates must appear in the email or are
worked out in code; tighter category definitions; drafts may not give opinions or claim actions), but they were made after seeing these emails, so read
these numbers as a demonstration, not a benchmark.</div>
</div>"""
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Morning brief</title><style>{CSS}</style></head><body>{page1}{page2}</body></html>"


if __name__ == "__main__":
    run = Path(sys.argv[1])
    (run / "brief.html").write_text(build(run))
    print(run / "brief.html")
