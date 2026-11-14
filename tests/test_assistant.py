"""Executive assistant: provider adapters, calendar logic, screening and an end-to-end run with a scripted model."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from assistant import demo_data as D
from assistant.agent import Masker, free_slots, resolve_date, screen
from assistant.demo import World, score
from assistant.agent import Assistant
from assistant.providers.base import Event
from assistant.providers.graph import GraphMailbox
from assistant.standins import GraphStandIn
from backoffice.llm import Scripted

TZ = D.TZ


# ------------------------------------------------------------------ unit
def test_resolve_weekday_without_date_uses_next_occurrence():
    d, notes = resolve_date({"date": None, "weekday": "Wednesday"}, D.NOW)  # NOW is Monday 16 Nov
    assert d.isoformat() == "2026-11-18" and notes


def test_resolve_flags_weekday_date_mismatch():
    d, notes = resolve_date({"date": "2026-11-19", "weekday": "Tuesday"}, D.NOW)
    assert d.isoformat() == "2026-11-19" and "check" in notes[0]


def test_free_slots_skip_busy_weekends_and_away_days():
    busy = [(datetime(2026, 11, 17, 10, tzinfo=TZ), datetime(2026, 11, 17, 11, 30, tzinfo=TZ)),   # Tuesday
            (datetime(2026, 11, 18, 6, 45, tzinfo=TZ), datetime(2026, 11, 18, 20, tzinfo=TZ))]     # Wednesday away
    got = free_slots(busy, datetime(2026, 11, 17, 10, 30, tzinfo=TZ), 45, 2, TZ)
    assert got == [datetime(2026, 11, 17, 11, 30, tzinfo=TZ), datetime(2026, 11, 19, 9, 0, tzinfo=TZ)]
    fri = free_slots([], datetime(2026, 11, 20, 16, 30, tzinfo=TZ), 60, 1, TZ)
    assert fri == [datetime(2026, 11, 23, 9, 0, tzinfo=TZ)]  # Friday too late, weekend skipped


def test_masker_hides_au_bank_details_and_restores():
    mk = Masker()
    s = mk.mask("Pay to BSB 062-914  Account 1098 4471 or call 0412 345 678")
    assert "062-914" not in s and "1098" not in s and "0412" not in s
    assert mk.restore(s).startswith("Pay to BSB 062-914")


def test_screen_catches_each_planted_attack_and_nothing_else():
    from email.utils import parseaddr
    from assistant.providers.mime import parse_raw
    hits = {}
    for acc in D.ACCOUNTS:
        for raw, m in D.messages_for(acc["key"]):
            msg = parse_raw(raw, account=acc["key"], provider="x", id="1")
            hits[m["subject"]] = bool(screen(msg, D.KNOWN[acc["key"]], D.INTERNAL_PEOPLE))
    expected = {m["subject"] for m in D.INBOX if m["truth"]["category"] == "risk"}
    assert {s for s, h in hits.items() if h} == expected


# ------------------------------------------------------------------ provider round trip (Graph shown; all four in the e2e test)
def test_graph_hold_uses_windows_timezone_and_tentative():
    si = GraphStandIn("sam@x.example")
    mb = GraphMailbox("k", "sam@x.example", "Sam", lambda: "t", client=httpx.Client(transport=httpx.MockTransport(si)))
    s = datetime(2026, 11, 20, 15, tzinfo=TZ)
    mb.add_hold(Event(s, s + timedelta(hours=1), "Review"))
    assert si.events[-1]["start"]["timeZone"] == "AUS Eastern Standard Time" and si.events[-1]["showAs"] == "tentative"
    got = mb.events(s - timedelta(hours=1), s + timedelta(hours=3))
    assert got[0].start == s


# ------------------------------------------------------------------ end to end with a scripted model
TRUTH = {m["subject"]: m for m in D.INBOX}


def oracle(system: str, user: str) -> str:
    if "Return one JSON object" not in system:
        return "Hi,\n\nThanks for this.\n\nCheers,\nSam"
    subj = re.search(r"Subject: (.*)", user).group(1).strip()
    m = TRUTH[subj]
    tr = m["truth"]
    js = {"category": tr["category"], "summary": subj, "meeting": None, "legs": [], "risk_reason": None}
    if tr["category"] == "meeting":
        js["meeting"] = {"title": subj, "date": tr["start"].date().isoformat(), "weekday": tr["start"].strftime("%A"), "time": tr["start"].strftime("%H:%M"),
                         "minutes": tr["minutes"], "location": None}
    if tr["category"] == "travel":
        js["legs"] = [{"label": f"leg {i}", "date": s.date().isoformat(), "depart": s.strftime("%H:%M"), "arrive": e.strftime("%H:%M")}
                      for i, (s, e) in enumerate(tr["legs"])]
    return json.dumps(js)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    w = World(tmp_path_factory.mktemp("world"))
    try:
        mbs = w.mailboxes()
        a = Assistant(mbs, {x["key"]: x["business"] for x in D.ACCOUNTS}, Scripted(oracle), D.NOW, D.SINCE, D.KNOWN, D.INTERNAL_PEOPLE,
                      {x["key"]: x["bookkeeping"] for x in D.ACCOUNTS}, D.OWNER).run()
        rb = {k: {"drafts": mb.drafts(), "events": mb.events(D.NOW, D.NOW + timedelta(days=21))} for k, mb in mbs.items()}
        yield a, rb, mbs
    finally:
        w.close()


def test_e2e_everything_lands_in_the_right_account(run):
    a, rb, _ = run
    sc = score(a, {k: {"drafts": v["drafts"]} for k, v in rb.items()})
    assert sc["category_correct"] == sc["emails"] == 35
    assert sc["meeting_time_correct"] == sc["meetings"] and sc["meeting_clash_correct"] == sc["meetings"]
    assert sc["travel_legs_correct"] == sc["travel"] and sc["risk_drafts"] == 0
    for it in a.items:
        if it.draft:
            assert any(d["subject"].lower().endswith(it.msg.subject.lower()) for d in rb[it.msg.account]["drafts"]), it.msg.subject
    # every hold is visible, as tentative, in the calendar of the account the email came to
    for h in a.holds:
        evs = rb[h["account"]]["events"]
        assert any(e.tentative and e.start == datetime.fromisoformat(h["start"]) for e in evs), h
    assert len(a.handoffs) == sum(m["truth"]["category"] == "accounts" for m in D.INBOX)


def test_e2e_clashes_offer_alternatives_and_add_no_hold(run):
    a, _, _ = run
    clashed = [i for i in a.items if i.meeting and i.meeting["clash"]]
    assert {i.msg.subject for i in clashed} == {"Meeting re Unit 5 lease dispute", "Newtown lease: meeting with landlord"}
    for i in clashed:
        assert len(i.meeting["alternatives"]) == 2 and "tentative hold added" not in i.actions
        assert all(x.weekday() < 5 and x.date() != datetime(2026, 11, 18).date() for x in i.meeting["alternatives"])  # not while in Melbourne


def test_e2e_travel_flags_clash_with_existing_lunch(run):
    a, _, _ = run
    mel = next(i for i in a.items if "SYD-MEL" in i.msg.subject)
    assert any("Lunch with Priya" in n for n in mel.notes)


def test_e2e_imap_draft_is_threaded(run):
    """The cPanel-style draft is a real IMAP message in Drafts, flagged \\Draft, threaded to the original."""
    import email
    a, _, mbs = run
    it = next(i for i in a.items if i.msg.account == "doyle" and i.draft)
    c = mbs["doyle"]._imap()
    try:
        c.select("Drafts", readonly=True)
        _, data = c.search(None, "DRAFT")
        raws = [c.fetch(n, "(BODY.PEEK[])")[1][0][1] for n in data[0].split()]
    finally:
        c.logout()
    heads = [email.message_from_bytes(r) for r in raws]
    assert any(h["In-Reply-To"] == it.msg.internet_id and h["Subject"] == "Re: " + it.msg.subject for h in heads)


def test_model_dates_must_be_written_in_the_email():
    text = "Saw you're down on Wednesday. Could we do 11:00 to 12:00?"
    d, notes = resolve_date({"date": "2026-11-15", "weekday": "Wednesday"}, D.NOW, text)
    assert d.isoformat() == "2026-11-18" and "not written" in notes[0]
    d, _ = resolve_date({"date": "2026-11-19", "weekday": "Thursday"}, D.NOW, "on Thursday 19 November at 3:00 pm")
    assert d.isoformat() == "2026-11-19"
    d, _ = resolve_date({"date": "2026-11-19", "weekday": None}, D.NOW, "no date here")
    assert d is None


def test_style_metric_reads_sam_signoffs():
    from assistant.demo import style
    assert style("Hi Tom,\n\nGo ahead.\n\nCheers,\nSam\n\nSam Doyle | Director, Harbourlane Property")["signed_sam"]
    assert style("Mark, agree. S")["signed_sam"] and not style("Best regards,\nSam Doyle")["signed_sam"]
