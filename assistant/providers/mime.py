"""RFC 5322 helpers shared by the Gmail and IMAP adapters (both hand us raw messages)."""
from __future__ import annotations

import email
import email.policy
import re
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses, make_msgid, parseaddr, parsedate_to_datetime
from datetime import datetime, timezone

from .base import Message, reply_subject


def html_to_text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def body_text(m) -> str:
    part = m.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    txt = part.get_content()
    return html_to_text(txt) if part.get_content_type() == "text/html" else txt.strip()


def parse_raw(raw: bytes, *, account: str, provider: str, id: str, thread_id: str = "") -> Message:
    m = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = parseaddr(str(m.get("From", "")))
    try:
        received = parsedate_to_datetime(str(m["Date"]))
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)
    return Message(account=account, provider=provider, id=id, thread_id=thread_id,
                   internet_id=str(m.get("Message-ID", "")).strip(), sender_name=name, sender=addr.lower(),
                   to=[a for _, a in getaddresses([str(m.get("To", ""))])], subject=str(m.get("Subject", "")),
                   received=received, body=body_text(m), references=str(m.get("References", "")).strip())


def build_reply(reply_to: Message, body: str, from_addr: str, from_name: str) -> bytes:
    """A reply with correct threading headers, so it lands in the same conversation in any client."""
    m = EmailMessage()
    m["From"] = f"{from_name} <{from_addr}>"
    m["To"] = f"{reply_to.sender_name} <{reply_to.sender}>" if reply_to.sender_name else reply_to.sender
    m["Subject"] = reply_subject(reply_to.subject)
    m["Date"] = format_datetime(datetime.now(timezone.utc))
    m["Message-ID"] = make_msgid(domain=from_addr.split("@")[-1])
    if reply_to.internet_id:
        m["In-Reply-To"] = reply_to.internet_id
        m["References"] = (reply_to.references + " " + reply_to.internet_id).strip()
    m.set_content(body)
    return bytes(m)
