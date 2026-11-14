"""Zoho Mail and Zoho Calendar REST APIs.

Zoho runs separate data centres; an Australian organisation uses mail.zoho.com.au and calendar.zoho.com.au
(and accounts.zoho.com.au for OAuth). Scopes: ZohoMail.messages.ALL, ZohoMail.folders.READ, ZohoCalendar.event.ALL.

Endpoints used:
  GET  /api/accounts/{accountId}/folders                                   folder ids (Inbox, Sent, Drafts)
  GET  /api/accounts/{accountId}/messages/view?folderId=..                 message list
  GET  /api/accounts/{accountId}/folders/{folderId}/messages/{id}/content  body
  GET  /api/accounts/{accountId}/folders/{folderId}/messages/{id}/header   headers (for Message-ID threading)
  POST /api/accounts/{accountId}/messages  {mode: "draft", inReplyTo}      threaded reply draft
  GET  calendar /api/v1/calendars/{uid}/events?range={start,end}           events (31-day maximum range)
  POST calendar /api/v1/calendars/{uid}/events?eventdata={...}             add a hold

Zoho Calendar has no "tentative" status in its event API, so holds are created with a "HOLD:" title prefix.
Message labelling is not wired up for Zoho in this version; tagged items are tracked in the assistant's own queue.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.parser import HeaderParser
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from .base import Event, Message, reply_subject
from .mime import html_to_text


def _ms(v) -> datetime:
    return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc)


def _zdt(s: str, tz: str) -> datetime:
    """Zoho event times: 20261117T100000+1100 or 20261117T100000Z."""
    if s.endswith("Z"):
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if re.search(r"[+-]\d{4}$", s):
        return datetime.strptime(s, "%Y%m%dT%H%M%S%z")
    return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(tz))


class ZohoMailbox:
    provider = "zoho"

    def __init__(self, account: str, address: str, owner: str, token: Callable[[], str], account_id: str, calendar_uid: str,
                 region: str = "com.au", tz: str = "Australia/Sydney", client: httpx.Client | None = None):
        self.account, self.address, self.owner, self.tz = account, address, owner, tz
        self.http = client or httpx.Client(timeout=30)
        self.token = token
        self.mail = f"https://mail.zoho.{region}/api/accounts/{account_id}"
        self.cal = f"https://calendar.zoho.{region}/api/v1/calendars/{calendar_uid}"
        self._folders: dict[str, str] | None = None

    def _h(self):
        return {"Authorization": f"Zoho-oauthtoken {self.token()}"}

    def _get(self, url, **params):
        r = self.http.get(url, params=params or None, headers=self._h())
        r.raise_for_status()
        return r.json()

    def folder(self, kind: str) -> str:
        if self._folders is None:
            self._folders = {}
            for f in self._get(f"{self.mail}/folders")["data"]:
                self._folders.setdefault(f["folderType"], f["folderId"])
        return self._folders[kind]

    def _messages(self, kind: str, limit: int) -> list[Message]:
        fid = self.folder(kind)
        rows = self._get(f"{self.mail}/messages/view", folderId=fid, limit=str(min(limit, 200)))["data"]
        out = []
        for v in rows:
            content = self._get(f"{self.mail}/folders/{fid}/messages/{v['messageId']}/content")["data"]["content"]
            hdr = self._get(f"{self.mail}/folders/{fid}/messages/{v['messageId']}/header")["data"]["headerContent"]
            h = HeaderParser().parsestr(hdr)
            out.append(Message(self.account, self.provider, str(v["messageId"]), str(v.get("threadId", "")),
                               str(h.get("Message-ID", "")).strip(), v.get("sender", ""), v.get("fromAddress", "").lower(),
                               [a.strip() for a in str(v.get("toAddress", "")).split(",") if a.strip()], v.get("subject", ""),
                               _ms(v["receivedTime"]), html_to_text(content), str(h.get("References", "")).strip()))
        return out

    def inbox(self, since: datetime) -> list[Message]:
        return [m for m in self._messages("Inbox", 200) if m.received >= since]

    def sent(self, limit: int = 50) -> list[Message]:
        return self._messages("Sent", limit)

    def drafts(self) -> list[dict]:
        fid = self.folder("Drafts")
        rows = self._get(f"{self.mail}/messages/view", folderId=fid, limit="200", includeto="true")["data"]
        return [{"id": str(v["messageId"]), "subject": v.get("subject", ""), "to": [v.get("toAddress", "")],
                 "body": html_to_text(self._get(f"{self.mail}/folders/{fid}/messages/{v['messageId']}/content")["data"]["content"])}
                for v in rows]

    def save_draft(self, reply_to: Message, body: str) -> str:
        payload = {"fromAddress": self.address, "toAddress": reply_to.sender, "subject": reply_subject(reply_to.subject),
                   "content": body, "mailFormat": "plaintext", "mode": "draft", "inReplyTo": reply_to.internet_id,
                   "refHeader": (reply_to.references + " " + reply_to.internet_id).strip()}
        r = self.http.post(f"{self.mail}/messages", headers=self._h(), json=payload)
        r.raise_for_status()
        return str(r.json()["data"]["messageId"])

    def events(self, start: datetime, end: datetime) -> list[Event]:
        out, s = [], start
        while s < end:  # Zoho caps a range at 31 days
            e = min(end, s + timedelta(days=31))
            rng = json.dumps({"start": s.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                              "end": e.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")})
            for v in self._get(f"{self.cal}/events", range=rng).get("events", []):
                if v.get("isallday"):
                    continue
                dt = v["dateandtime"]
                tz = dt.get("timezone", self.tz)
                out.append(Event(_zdt(dt["start"], tz), _zdt(dt["end"], tz), v.get("title", ""), v.get("location", ""),
                                 v.get("title", "").startswith("HOLD:"), v.get("uid", "")))
            s = e
        return out

    def add_hold(self, ev: Event, note: str = "") -> str:
        data = {"title": f"HOLD: {ev.title}", "description": note, "location": ev.location, "transparency": 0,
                "dateandtime": {"start": ev.start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                                "end": ev.end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "timezone": self.tz}}
        r = self.http.post(f"{self.cal}/events", params={"eventdata": json.dumps(data)}, headers=self._h())
        r.raise_for_status()
        return r.json()["events"][0]["uid"]

    def tag(self, msg: Message, label: str) -> None:
        return None
