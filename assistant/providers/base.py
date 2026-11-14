"""Canonical records and the interface every mail/calendar provider implements.

The assistant never sends email and never accepts invitations on anyone's behalf. Its only write actions are:
  * save a reply as a draft in the account's own Drafts folder (the person sends it from their normal client),
  * put a tentative hold in the account's own calendar,
  * tag a message so it can be found again (category, label or keyword, depending on the provider).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Message:
    account: str            # account key, e.g. "harbourlane"
    provider: str           # m365 | google | zoho | imap
    id: str                 # provider's own message id
    thread_id: str          # provider's conversation/thread id ("" if none)
    internet_id: str        # RFC 5322 Message-ID, used for In-Reply-To
    sender_name: str
    sender: str             # email address
    to: list[str]
    subject: str
    received: datetime      # timezone-aware
    body: str               # plain text
    references: str = ""

    def to_dict(self):
        d = asdict(self)
        d["received"] = self.received.isoformat()
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["received"] = datetime.fromisoformat(d["received"])
        return cls(**d)


@dataclass
class Event:
    start: datetime         # timezone-aware
    end: datetime
    title: str
    location: str = ""
    tentative: bool = False
    id: str = ""

    def to_dict(self):
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["start"], d["end"] = datetime.fromisoformat(d["start"]), datetime.fromisoformat(d["end"])
        return cls(**d)


@dataclass
class Draft:
    in_reply_to: Message
    body: str
    id: str = ""


class MailCalendar(Protocol):
    """What the assistant needs from any provider. Four methods to read, three to write."""

    provider: str
    account: str
    address: str

    def inbox(self, since: datetime) -> list[Message]: ...
    def sent(self, limit: int = 50) -> list[Message]: ...
    def events(self, start: datetime, end: datetime) -> list[Event]: ...
    def drafts(self) -> list[dict]: ...

    def save_draft(self, reply_to: Message, body: str) -> str: ...
    def add_hold(self, ev: Event, note: str = "") -> str: ...
    def tag(self, msg: Message, label: str) -> None: ...


def reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


@dataclass
class AccountSpec:
    key: str
    provider: str
    address: str
    owner: str
    business: str
    settings: dict = field(default_factory=dict)
