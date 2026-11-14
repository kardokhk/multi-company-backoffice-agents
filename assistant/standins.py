"""Local stand-ins for the Microsoft Graph, Gmail/Google Calendar and Zoho Mail/Calendar APIs.

They implement only the endpoints the adapters call, with the request and response shapes from each provider's
documentation, and they reject malformed write requests (wrong time-zone name, missing threading header, a hold that
is not tentative). The adapters run against them through ``httpx.MockTransport``, so the demo exercises the same
adapter code that would talk to the real services; only the transport differs.
"""
from __future__ import annotations

import base64
import email
import email.policy
import email.utils
import itertools
import json
import re
from datetime import datetime, timezone
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from .providers.graph import IANA_TZ

_ids = itertools.count(1000)


def _nid(prefix):
    return f"{prefix}{next(_ids)}"


def _bad(msg):
    return httpx.Response(400, json={"error": {"message": msg}})


def _auth(req, scheme="Bearer"):
    return req.headers.get("Authorization", "").startswith(scheme + " ")


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


def _text(m):
    p = m.get_body(preferencelist=("plain",))
    return p.get_content().strip() if p else ""


# =================================================================== Microsoft Graph
class GraphStandIn:
    def __init__(self, upn: str):
        self.upn = upn
        self.folders = {"inbox": [], "sentitems": [], "drafts": []}
        self.events = []
        self.calls = []

    def load(self, folder: str, raw: bytes):
        m = _parse(raw)
        self.folders[folder].append({
            "id": _nid("AAMk"), "conversationId": _nid("AAQk"), "internetMessageId": str(m["Message-ID"]),
            "subject": str(m["Subject"]), "categories": [],
            "from": {"emailAddress": {"name": email.utils.parseaddr(str(m["From"]))[0], "address": email.utils.parseaddr(str(m["From"]))[1]}},
            "toRecipients": [{"emailAddress": {"address": a}} for _, a in email.utils.getaddresses([str(m["To"])])],
            "receivedDateTime": email.utils.parsedate_to_datetime(str(m["Date"])).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": {"contentType": "text", "content": _text(m)}})

    def add_event(self, start, end, subject, location=""):
        self.events.append({"id": _nid("AAMkEv"), "subject": subject, "showAs": "busy", "location": {"displayName": location},
                            "start": {"dateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
                            "end": {"dateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}})

    def _find(self, mid):
        for f, rows in self.folders.items():
            for r in rows:
                if r["id"] == mid:
                    return f, r
        return None, None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if not _auth(req):
            return httpx.Response(401)
        p = req.url.path.removeprefix(f"/v1.0/users/{self.upn}")
        q = req.url.params
        m = re.fullmatch(r"/mailFolders/(\w+)/messages", p)
        if req.method == "GET" and m:
            rows = self.folders[m.group(1)]
            flt = q.get("$filter", "")
            if flt.startswith("receivedDateTime ge "):
                cut = flt.split(" ge ")[1]
                rows = [r for r in rows if r["receivedDateTime"] >= cut]
            return httpx.Response(200, json={"value": rows})
        m = re.fullmatch(r"/messages/([^/]+)/createReply", p)
        if req.method == "POST" and m:
            _, orig = self._find(m.group(1))
            if orig is None:
                return httpx.Response(404)
            d = {"id": _nid("AAMkDr"), "conversationId": orig["conversationId"], "subject": "RE: " + orig["subject"],
                 "toRecipients": [{"emailAddress": orig["from"]["emailAddress"]}], "body": {"contentType": "text", "content": ""},
                 "inReplyTo": orig["internetMessageId"]}
            self.folders["drafts"].append(d)
            return httpx.Response(201, json=d)
        m = re.fullmatch(r"/messages/([^/]+)", p)
        if req.method == "PATCH" and m:
            _, row = self._find(m.group(1))
            if row is None:
                return httpx.Response(404)
            row.update(json.loads(req.content))
            return httpx.Response(200, json=row)
        if req.method == "GET" and p == "/calendarView":
            s = datetime.fromisoformat(q["startDateTime"])
            e = datetime.fromisoformat(q["endDateTime"])
            def utc(v):
                return datetime.fromisoformat(v["dateTime"][:19]).replace(tzinfo=ZoneInfo(IANA_TZ.get(v["timeZone"], v["timeZone"])))
            return httpx.Response(200, json={"value": [v for v in self.events if utc(v["start"]) < e and utc(v["end"]) > s]})
        if req.method == "POST" and p == "/events":
            body = json.loads(req.content)
            for k in ("start", "end"):
                if body[k]["timeZone"] not in IANA_TZ:
                    return _bad(f"unsupported timeZone {body[k]['timeZone']}")
            if body.get("showAs") != "tentative":
                return _bad("assistant holds must be tentative")
            body["id"] = _nid("AAMkEv")
            self.events.append(body)
            return httpx.Response(201, json=body)
        return httpx.Response(404, json={"error": {"message": f"no route {req.method} {p}"}})


# =================================================================== Gmail + Google Calendar
class GoogleStandIn:
    def __init__(self, address: str):
        self.address = address
        self.msgs = {}           # id -> {raw, threadId, labelIds, internalDate}
        self.drafts = {}
        self.labels = {"INBOX": "INBOX", "SENT": "SENT"}
        self.events = []
        self.calls = []

    def load(self, label: str, raw: bytes):
        m = _parse(raw)
        mid = _nid("18c")
        self.msgs[mid] = {"raw": raw, "threadId": _nid("18t"), "labelIds": [label],
                          "internalDate": int(email.utils.parsedate_to_datetime(str(m["Date"])).timestamp())}

    def add_event(self, start, end, summary, location=""):
        self.events.append({"id": _nid("ev"), "summary": summary, "location": location, "status": "confirmed",
                            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if not _auth(req):
            return httpx.Response(401)
        host, p, q = req.url.host, req.url.path, req.url.params
        if host == "gmail.googleapis.com":
            p = p.removeprefix("/gmail/v1/users/me")
            if req.method == "GET" and p == "/messages":
                query = q.get("q", "")
                lab = "INBOX" if "in:inbox" in query else "SENT" if "in:sent" in query else None
                after = int(re.search(r"after:(\d+)", query).group(1)) if "after:" in query else 0
                rows = [(k, v) for k, v in self.msgs.items() if (lab is None or lab in v["labelIds"]) and v["internalDate"] > after]
                rows.sort(key=lambda kv: -kv[1]["internalDate"])
                return httpx.Response(200, json={"messages": [{"id": k, "threadId": v["threadId"]} for k, v in rows],
                                                 "resultSizeEstimate": len(rows)})
            m = re.fullmatch(r"/messages/([^/]+)", p)
            if req.method == "GET" and m:
                v = self.msgs[m.group(1)]
                return httpx.Response(200, json={"id": m.group(1), "threadId": v["threadId"], "labelIds": v["labelIds"],
                                                 "raw": base64.urlsafe_b64encode(v["raw"]).decode().rstrip("=")})
            m = re.fullmatch(r"/messages/([^/]+)/modify", p)
            if req.method == "POST" and m:
                v = self.msgs[m.group(1)]
                for lid in json.loads(req.content).get("addLabelIds", []):
                    if lid not in self.labels.values():
                        return _bad(f"unknown label {lid}")
                    v["labelIds"].append(lid)
                return httpx.Response(200, json={"id": m.group(1), "labelIds": v["labelIds"]})
            if p == "/labels" and req.method == "GET":
                return httpx.Response(200, json={"labels": [{"id": i, "name": n} for n, i in self.labels.items()]})
            if p == "/labels" and req.method == "POST":
                name = json.loads(req.content)["name"]
                self.labels[name] = _nid("Label_")
                return httpx.Response(200, json={"id": self.labels[name], "name": name})
            if p == "/drafts" and req.method == "POST":
                msg = json.loads(req.content)["message"]
                raw = base64.urlsafe_b64decode(msg["raw"] + "=" * (-len(msg["raw"]) % 4))
                parsed = _parse(raw)
                if not parsed["In-Reply-To"]:
                    return _bad("reply draft without In-Reply-To")
                if msg.get("threadId") not in {v["threadId"] for v in self.msgs.values()}:
                    return _bad("unknown threadId")
                did = _nid("r-")
                self.drafts[did] = {"raw": raw, "threadId": msg["threadId"]}
                return httpx.Response(200, json={"id": did, "message": {"id": _nid("18d"), "threadId": msg["threadId"]}})
            if p == "/drafts" and req.method == "GET":
                return httpx.Response(200, json={"drafts": [{"id": k} for k in self.drafts]})
            m = re.fullmatch(r"/drafts/([^/]+)", p)
            if req.method == "GET" and m:
                d = self.drafts[m.group(1)]
                return httpx.Response(200, json={"id": m.group(1), "message": {"threadId": d["threadId"],
                                                 "raw": base64.urlsafe_b64encode(d["raw"]).decode().rstrip("=")}})
        if host == "www.googleapis.com" and p.startswith("/calendar/v3/calendars/primary/events"):
            if req.method == "GET":
                s, e = datetime.fromisoformat(q["timeMin"]), datetime.fromisoformat(q["timeMax"])
                rows = [v for v in self.events if datetime.fromisoformat(v["start"]["dateTime"]) < e
                        and datetime.fromisoformat(v["end"]["dateTime"]) > s]
                return httpx.Response(200, json={"items": sorted(rows, key=lambda v: v["start"]["dateTime"])})
            body = json.loads(req.content)
            if body.get("status") != "tentative":
                return _bad("assistant holds must be tentative")
            ZoneInfo(body["start"]["timeZone"])
            body["id"] = _nid("ev")
            self.events.append(body)
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"message": f"no route {req.method} {host}{p}"}})


# =================================================================== Zoho Mail + Zoho Calendar
class ZohoStandIn:
    def __init__(self, account_id: str, calendar_uid: str):
        self.aid, self.cuid = account_id, calendar_uid
        self.fids = {"Inbox": "9000000002014", "Drafts": "9000000002016", "Sent": "9000000002022"}
        self.msgs = {k: [] for k in self.fids}
        self.events = []
        self.calls = []

    def load(self, folder: str, raw: bytes):
        m = _parse(raw)
        hdr = "".join(f"{k}: {v}\r\n" for k, v in m.items())
        self.msgs[folder].append({"messageId": _nid("17"), "threadId": _nid("17t"), "folderId": self.fids[folder],
                                  "fromAddress": email.utils.parseaddr(str(m["From"]))[1], "sender": email.utils.parseaddr(str(m["From"]))[0],
                                  "toAddress": email.utils.parseaddr(str(m["To"]))[1], "subject": str(m["Subject"]),
                                  "summary": _text(m)[:100], "content": "<div>" + _text(m).replace("\n", "<br>") + "</div>",
                                  "receivedTime": str(int(email.utils.parsedate_to_datetime(str(m["Date"])).timestamp() * 1000)),
                                  "header": hdr})

    def add_event(self, start, end, title, location=""):
        f = "%Y%m%dT%H%M%S%z"
        self.events.append({"uid": _nid("ev") + "@zoho.com", "title": title, "location": location, "isallday": False,
                            "dateandtime": {"timezone": "Australia/Sydney", "start": start.strftime(f), "end": end.strftime(f)}})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if not _auth(req, "Zoho-oauthtoken"):
            return httpx.Response(401)
        host, p, q = req.url.host, req.url.path, req.url.params
        if host == "mail.zoho.com.au":
            p = p.removeprefix(f"/api/accounts/{self.aid}")
            if p == "/folders":
                return httpx.Response(200, json={"status": {"code": 200}, "data": [
                    {"folderName": k, "folderType": k, "folderId": v, "path": "/" + k} for k, v in self.fids.items()]})
            if p == "/messages/view":
                folder = next(k for k, v in self.fids.items() if v == q["folderId"])
                rows = [{k: v for k, v in r.items() if k not in ("content", "header")} for r in self.msgs[folder]]
                return httpx.Response(200, json={"status": {"code": 200}, "data": rows[: int(q.get("limit", 10))]})
            m = re.fullmatch(r"/folders/(\d+)/messages/(\d+)/(content|header)", p)
            if m:
                folder = next(k for k, v in self.fids.items() if v == m.group(1))
                row = next(r for r in self.msgs[folder] if r["messageId"] == m.group(2))
                key = "content" if m.group(3) == "content" else "headerContent"
                return httpx.Response(200, json={"status": {"code": 200}, "data": {key: row[m.group(3)]}})
            if p == "/messages" and req.method == "POST":
                b = json.loads(req.content)
                if b.get("mode") != "draft":
                    return _bad("assistant may only save drafts")
                if not b.get("inReplyTo"):
                    return _bad("reply draft without inReplyTo")
                for k in ("fromAddress", "toAddress"):
                    if not b.get(k):
                        return _bad(f"{k} missing")
                row = {"messageId": _nid("17"), "folderId": self.fids["Drafts"], "fromAddress": b["fromAddress"], "toAddress": b["toAddress"],
                       "subject": b.get("subject", ""), "content": b.get("content", ""), "header": f"In-Reply-To: {b['inReplyTo']}\r\n",
                       "receivedTime": "0", "sender": ""}
                self.msgs["Drafts"].append(row)
                return httpx.Response(200, json={"status": {"code": 200}, "data": {"messageId": row["messageId"]}})
        if host == "calendar.zoho.com.au" and p == f"/api/v1/calendars/{self.cuid}/events":
            if req.method == "GET":
                rng = json.loads(q["range"])
                s = datetime.strptime(rng["start"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                e = datetime.strptime(rng["end"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                if (e - s).days > 31:
                    return _bad("range cannot exceed 31 days")
                f = "%Y%m%dT%H%M%S%z"
                rows = [v for v in self.events if datetime.strptime(v["dateandtime"]["start"], f) < e
                        and datetime.strptime(v["dateandtime"]["end"], f) > s]
                return httpx.Response(200, json={"events": rows})
            data = json.loads(unquote(q["eventdata"]))
            dt = data.get("dateandtime", {})
            for k in ("start", "end"):
                if not re.fullmatch(r"\d{8}T\d{6}Z", dt.get(k, "")):
                    return _bad(f"dateandtime.{k} must be yyyyMMdd'T'HHmmss'Z'")
            utc = timezone.utc
            ev = {"uid": _nid("ev") + "@zoho.com", "title": data["title"], "location": data.get("location", ""), "isallday": False,
                  "dateandtime": {"timezone": dt.get("timezone", "UTC"),
                                  "start": datetime.strptime(dt["start"], "%Y%m%dT%H%M%SZ").replace(tzinfo=utc).strftime("%Y%m%dT%H%M%S%z"),
                                  "end": datetime.strptime(dt["end"], "%Y%m%dT%H%M%SZ").replace(tzinfo=utc).strftime("%Y%m%dT%H%M%S%z")}}
            self.events.append(ev)
            return httpx.Response(200, json={"events": [ev]})
        return httpx.Response(404, json={"error": f"no route {req.method} {host}{p}"})
