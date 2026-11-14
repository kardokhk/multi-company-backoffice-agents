"""Microsoft 365 (Exchange Online) through Microsoft Graph v1.0.

Production setup: an Entra ID app registration with application permissions, limited to the named mailboxes
with Exchange Online RBAC for Applications (a management scope), so the app cannot read any other mailbox
in the tenant. Tokens come from the client-credentials flow; ``token`` below is a callable returning one.

Endpoints used:
  GET   /users/{upn}/mailFolders/inbox/messages        read new mail
  GET   /users/{upn}/mailFolders/sentitems/messages    read sent mail (tone examples)
  GET   /users/{upn}/mailFolders/drafts/messages       list drafts
  POST  /users/{upn}/messages/{id}/createReply         create a threaded reply draft
  PATCH /users/{upn}/messages/{id}                     set the draft body; set categories on a message
  GET   /users/{upn}/calendarView                      existing events in a window
  POST  /users/{upn}/events                            add a hold with showAs = tentative
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from .base import Event, Message
from .mime import html_to_text

BASE = "https://graph.microsoft.com/v1.0"
SELECT = "id,conversationId,internetMessageId,subject,from,toRecipients,receivedDateTime,body"


# Graph's dateTimeTimeZone uses Windows time-zone names; calendarView returns UTC unless asked otherwise.
WINDOWS_TZ = {"Australia/Sydney": "AUS Eastern Standard Time", "Australia/Melbourne": "AUS Eastern Standard Time",
              "Australia/Brisbane": "E. Australia Standard Time", "Australia/Adelaide": "Cen. Australia Standard Time",
              "Australia/Perth": "W. Australia Standard Time", "UTC": "UTC"}
IANA_TZ = {v: k for k, v in reversed(list(WINDOWS_TZ.items()))}


def _zone(name: str) -> ZoneInfo:
    return ZoneInfo(IANA_TZ.get(name, name))


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class GraphMailbox:
    provider = "m365"

    def __init__(self, account: str, upn: str, owner: str, token: Callable[[], str], tz: str = "Australia/Sydney",
                 client: httpx.Client | None = None):
        self.account, self.address, self.owner, self.tz = account, upn, owner, tz
        self.http = client or httpx.Client(timeout=30)
        self.token = token
        self.u = f"{BASE}/users/{upn}"

    def _h(self, **extra):
        return {"Authorization": f"Bearer {self.token()}", "Prefer": 'outlook.body-content-type="text"', **extra}

    def _get_all(self, url, params=None):
        out = []
        while url:
            r = self.http.get(url, params=params, headers=self._h())
            r.raise_for_status()
            js = r.json()
            out += js.get("value", [])
            url, params = js.get("@odata.nextLink"), None
        return out

    def _msg(self, v) -> Message:
        body = v.get("body", {})
        text = body.get("content", "")
        if body.get("contentType", "").lower() == "html":
            text = html_to_text(text)
        frm = v.get("from", {}).get("emailAddress", {})
        return Message(self.account, self.provider, v["id"], v.get("conversationId", ""), v.get("internetMessageId", ""),
                       frm.get("name", ""), frm.get("address", "").lower(),
                       [r["emailAddress"]["address"] for r in v.get("toRecipients", [])], v.get("subject", ""),
                       _dt(v["receivedDateTime"]), text.strip())

    def inbox(self, since: datetime) -> list[Message]:
        flt = f"receivedDateTime ge {since.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        vals = self._get_all(f"{self.u}/mailFolders/inbox/messages", {"$filter": flt, "$select": SELECT, "$top": "50"})
        return [self._msg(v) for v in vals]

    def sent(self, limit: int = 50) -> list[Message]:
        vals = self._get_all(f"{self.u}/mailFolders/sentitems/messages", {"$select": SELECT, "$top": str(limit)})
        return [self._msg(v) for v in vals][:limit]

    def drafts(self) -> list[dict]:
        vals = self._get_all(f"{self.u}/mailFolders/drafts/messages", {"$select": "id,subject,body,toRecipients"})
        return [{"id": v["id"], "subject": v.get("subject", ""), "body": v.get("body", {}).get("content", ""),
                 "to": [r["emailAddress"]["address"] for r in v.get("toRecipients", [])]} for v in vals]

    def save_draft(self, reply_to: Message, body: str) -> str:
        r = self.http.post(f"{self.u}/messages/{reply_to.id}/createReply", headers=self._h(), json={})
        r.raise_for_status()
        did = r.json()["id"]
        r = self.http.patch(f"{self.u}/messages/{did}", headers=self._h(), json={"body": {"contentType": "Text", "content": body}})
        r.raise_for_status()
        return did

    def events(self, start: datetime, end: datetime) -> list[Event]:
        params = {"startDateTime": start.astimezone(timezone.utc).isoformat(), "endDateTime": end.astimezone(timezone.utc).isoformat(),
                  "$select": "id,subject,start,end,location,showAs"}
        vals = self._get_all(f"{self.u}/calendarView", params)
        out = []
        for v in vals:
            s = datetime.fromisoformat(v["start"]["dateTime"][:19]).replace(tzinfo=_zone(v["start"].get("timeZone", "UTC")))
            e = datetime.fromisoformat(v["end"]["dateTime"][:19]).replace(tzinfo=_zone(v["end"].get("timeZone", "UTC")))
            out.append(Event(s, e, v.get("subject", ""), v.get("location", {}).get("displayName", ""),
                             v.get("showAs") == "tentative", v["id"]))
        return out

    def add_hold(self, ev: Event, note: str = "") -> str:
        z = ZoneInfo(self.tz)
        payload = {"subject": ev.title, "body": {"contentType": "Text", "content": note},
                   "start": {"dateTime": ev.start.astimezone(z).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": WINDOWS_TZ.get(self.tz, self.tz)},
                   "end": {"dateTime": ev.end.astimezone(z).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": WINDOWS_TZ.get(self.tz, self.tz)},
                   "location": {"displayName": ev.location}, "showAs": "tentative", "isReminderOn": True}
        r = self.http.post(f"{self.u}/events", headers=self._h(), json=payload)
        r.raise_for_status()
        return r.json()["id"]

    def tag(self, msg: Message, label: str) -> None:
        r = self.http.patch(f"{self.u}/messages/{msg.id}", headers=self._h(), json={"categories": [label]})
        r.raise_for_status()
