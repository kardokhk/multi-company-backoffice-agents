"""Any host that speaks the open standards: IMAP for mail, CalDAV for calendars.

This is the route for cPanel-hosted email (Dovecot IMAP on 993; cPanel Calendars and Contacts serves CalDAV on
port 2080 with the full email address and its password). It also works as a fallback for Microsoft 365, Google and
Zoho, which all offer IMAP and CalDAV, but their own APIs are preferred there (finer permissions, OAuth, threading).

Write actions:
  * drafts: IMAP APPEND to the Drafts folder with the \\Draft flag and In-Reply-To/References headers,
  * holds: a VEVENT with STATUS:TENTATIVE in the account's calendar,
  * tags:  an IMAP keyword on the message (shown as a tag in Thunderbird and most clients).
cPanel names folders with an "INBOX." prefix (INBOX.Sent, INBOX.Drafts); the special-use flags from LIST are used
to find them, with configurable fallbacks.
"""
from __future__ import annotations

import imaplib
import re
import time
from datetime import datetime, timezone

from .base import Event, Message
from .mime import build_reply, parse_raw

SPECIAL = re.compile(rb'\((?P<flags>[^)]*)\) "(?P<delim>[^"]*)" (?P<name>.+)$')


class ImapCalDavMailbox:
    provider = "imap"

    def __init__(self, account: str, address: str, owner: str, password: str, imap_host: str, imap_port: int = 993,
                 caldav_url: str | None = None, ssl: bool = True, sent_folder: str | None = None, drafts_folder: str | None = None,
                 tz: str = "Australia/Sydney"):
        self.account, self.address, self.owner, self.tz = account, address, owner, tz
        self.host, self.port, self.ssl, self.password = imap_host, imap_port, ssl, password
        self.caldav_url = caldav_url
        self._sent, self._drafts = sent_folder, drafts_folder
        self._cal = None

    # ------------------------------------------------------------ IMAP
    def _imap(self):
        c = imaplib.IMAP4_SSL(self.host, self.port) if self.ssl else imaplib.IMAP4(self.host, self.port)
        c.login(self.address, self.password)
        return c

    def _special(self, c, flag: bytes, fallbacks: list[str]) -> str:
        typ, rows = c.list()
        names = []
        for r in rows or []:
            m = SPECIAL.match(r or b"")
            if not m:
                continue
            name = m.group("name").decode().strip().strip('"')
            names.append(name)
            if flag in m.group("flags"):
                return name
        for f in fallbacks:
            if f in names:
                return f
        c.create(fallbacks[0])
        return fallbacks[0]

    def _folder(self, c, kind: str) -> str:
        if kind == "sent":
            self._sent = self._sent or self._special(c, b"\\Sent", ["Sent", "INBOX.Sent", "Sent Items"])
            return self._sent
        self._drafts = self._drafts or self._special(c, b"\\Drafts", ["Drafts", "INBOX.Drafts"])
        return self._drafts

    def _fetch(self, c, folder: str, criteria: str, limit: int) -> list[Message]:
        c.select(f'"{folder}"', readonly=True)
        typ, data = c.uid("SEARCH", None, criteria)
        uids = (data[0] or b"").split()[-limit:]
        out = []
        for uid in uids:
            typ, parts = c.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next(p[1] for p in parts if isinstance(p, tuple))
            out.append(parse_raw(raw, account=self.account, provider=self.provider, id=f"{folder}:{uid.decode()}"))
        return out

    def inbox(self, since: datetime) -> list[Message]:
        c = self._imap()
        try:
            msgs = self._fetch(c, "INBOX", f"SINCE {since.strftime('%d-%b-%Y')}", 500)
        finally:
            c.logout()
        return [m for m in msgs if m.received >= since]  # IMAP SINCE is date-only

    def sent(self, limit: int = 50) -> list[Message]:
        c = self._imap()
        try:
            return self._fetch(c, self._folder(c, "sent"), "ALL", limit)
        finally:
            c.logout()

    def drafts(self) -> list[dict]:
        c = self._imap()
        try:
            msgs = self._fetch(c, self._folder(c, "drafts"), "ALL", 500)
        finally:
            c.logout()
        return [{"id": m.id, "subject": m.subject, "body": m.body, "to": m.to} for m in msgs]

    def save_draft(self, reply_to: Message, body: str) -> str:
        c = self._imap()
        try:
            folder = self._folder(c, "drafts")
            typ, data = c.append(f'"{folder}"', "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()),
                                 build_reply(reply_to, body, self.address, self.owner))
            m = re.search(rb"APPENDUID \d+ (\d+)", data[0] or b"")
            return f"{folder}:{m.group(1).decode()}" if m else folder
        finally:
            c.logout()

    def tag(self, msg: Message, label: str) -> None:
        folder, uid = msg.id.rsplit(":", 1)
        kw = "$" + re.sub(r"[^A-Za-z0-9]", "_", label)
        c = self._imap()
        try:
            c.select(f'"{folder}"')
            c.uid("STORE", uid, "+FLAGS", f"({kw})")
        finally:
            c.logout()

    # ------------------------------------------------------------ CalDAV
    def _calendar(self):
        if self._cal is None:
            import caldav
            client = caldav.DAVClient(url=self.caldav_url, username=self.address, password=self.password)
            cals = client.principal().calendars()
            self._cal = cals[0] if cals else client.principal().make_calendar(name="Calendar")
        return self._cal

    def events(self, start: datetime, end: datetime) -> list[Event]:
        if not self.caldav_url:
            return []
        out = []
        for e in self._calendar().search(start=start, end=end, event=True, expand=True):
            v = e.icalendar_component
            s, f = v.get("dtstart").dt, v.get("dtend").dt
            if not isinstance(s, datetime):
                continue
            s = s if s.tzinfo else s.replace(tzinfo=timezone.utc)
            f = f if f.tzinfo else f.replace(tzinfo=timezone.utc)
            out.append(Event(s, f, str(v.get("summary", "")), str(v.get("location", "")),
                             str(v.get("status", "")).upper() == "TENTATIVE", str(v.get("uid", ""))))
        return out

    def add_hold(self, ev: Event, note: str = "") -> str:
        if not self.caldav_url:
            raise RuntimeError(f"{self.account}: no CalDAV URL configured")
        e = self._calendar().save_event(dtstart=ev.start, dtend=ev.end, summary=ev.title, location=ev.location,
                                        description=note, status="TENTATIVE")
        return str(e.icalendar_component.get("uid", ""))
