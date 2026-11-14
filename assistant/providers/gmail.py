"""Google Workspace through the Gmail API v1 and Calendar API v3.

Production setup: an OAuth client of type "Internal" in the company's own Workspace, allowed by the admin under
API controls, with the user consenting once (scopes gmail.modify and calendar.events). Internal apps stay inside
the organisation; no domain-wide delegation is needed for a single executive's mailbox.

Endpoints used:
  GET  gmail/v1/users/me/messages?q=...                 ids of new or sent mail
  GET  gmail/v1/users/me/messages/{id}?format=raw       full RFC 5322 message
  GET  gmail/v1/users/me/drafts, drafts/{id}            list drafts
  POST gmail/v1/users/me/drafts                         threaded reply draft (raw + threadId)
  GET/POST gmail/v1/users/me/labels                     find or create the assistant's labels
  POST gmail/v1/users/me/messages/{id}/modify           apply a label
  GET  calendar/v3/calendars/primary/events             existing events (singleEvents=true)
  POST calendar/v3/calendars/primary/events             add a hold with status = tentative
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Callable

import httpx

from .base import Event, Message
from .mime import build_reply, parse_raw

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary"


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class GmailMailbox:
    provider = "google"

    def __init__(self, account: str, address: str, owner: str, token: Callable[[], str], tz: str = "Australia/Sydney",
                 client: httpx.Client | None = None):
        self.account, self.address, self.owner, self.tz = account, address, owner, tz
        self.http = client or httpx.Client(timeout=30)
        self.token = token
        self._labels: dict[str, str] | None = None

    def _h(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def _get(self, url, **params):
        r = self.http.get(url, params=params or None, headers=self._h())
        r.raise_for_status()
        return r.json()

    def _list(self, q: str, limit: int) -> list[Message]:
        ids, token = [], None
        while len(ids) < limit:
            p = {"q": q, "maxResults": str(min(100, limit))}
            if token:
                p["pageToken"] = token
            js = self._get(f"{GMAIL}/messages", **p)
            ids += [(m["id"], m.get("threadId", "")) for m in js.get("messages", [])]
            token = js.get("nextPageToken")
            if not token:
                break
        out = []
        for mid, tid in ids[:limit]:
            js = self._get(f"{GMAIL}/messages/{mid}", format="raw")
            out.append(parse_raw(_b64d(js["raw"]), account=self.account, provider=self.provider, id=mid, thread_id=js.get("threadId", tid)))
        return out

    def inbox(self, since: datetime) -> list[Message]:
        return [m for m in self._list(f"in:inbox after:{int(since.timestamp())}", 200) if m.received >= since]

    def sent(self, limit: int = 50) -> list[Message]:
        return self._list("in:sent", limit)

    def drafts(self) -> list[dict]:
        out = []
        for d in self._get(f"{GMAIL}/drafts").get("drafts", []):
            js = self._get(f"{GMAIL}/drafts/{d['id']}", format="raw")
            m = parse_raw(_b64d(js["message"]["raw"]), account=self.account, provider=self.provider, id=d["id"])
            out.append({"id": d["id"], "subject": m.subject, "body": m.body, "to": m.to})
        return out

    def save_draft(self, reply_to: Message, body: str) -> str:
        raw = base64.urlsafe_b64encode(build_reply(reply_to, body, self.address, self.owner)).decode()
        r = self.http.post(f"{GMAIL}/drafts", headers=self._h(), json={"message": {"raw": raw, "threadId": reply_to.thread_id}})
        r.raise_for_status()
        return r.json()["id"]

    def events(self, start: datetime, end: datetime) -> list[Event]:
        js = self._get(f"{CAL}/events", timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents="true", orderBy="startTime")
        out = []
        for v in js.get("items", []):
            if "dateTime" not in v.get("start", {}):
                continue  # all-day items do not block time slots
            out.append(Event(datetime.fromisoformat(v["start"]["dateTime"]), datetime.fromisoformat(v["end"]["dateTime"]),
                             v.get("summary", ""), v.get("location", ""), v.get("status") == "tentative", v["id"]))
        return out

    def add_hold(self, ev: Event, note: str = "") -> str:
        payload = {"summary": ev.title, "description": note, "location": ev.location, "status": "tentative",
                   "start": {"dateTime": ev.start.isoformat(), "timeZone": self.tz},
                   "end": {"dateTime": ev.end.isoformat(), "timeZone": self.tz}}
        r = self.http.post(f"{CAL}/events", headers=self._h(), json=payload)
        r.raise_for_status()
        return r.json()["id"]

    def _label_id(self, name: str) -> str:
        if self._labels is None:
            self._labels = {l["name"]: l["id"] for l in self._get(f"{GMAIL}/labels").get("labels", [])}
        if name not in self._labels:
            r = self.http.post(f"{GMAIL}/labels", headers=self._h(), json={"name": name, "labelListVisibility": "labelShow",
                                                                           "messageListVisibility": "show"})
            r.raise_for_status()
            self._labels[name] = r.json()["id"]
        return self._labels[name]

    def tag(self, msg: Message, label: str) -> None:
        r = self.http.post(f"{GMAIL}/messages/{msg.id}/modify", headers=self._h(), json={"addLabelIds": [self._label_id(label)]})
        r.raise_for_status()
