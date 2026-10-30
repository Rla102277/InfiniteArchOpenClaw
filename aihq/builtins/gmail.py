"""Gmail over IMAP and SMTP with an app password. Search, read, draft, send."""
from __future__ import annotations

import email
import email.utils
import html
import imaplib
import re
import smtplib
import time
from email.header import decode_header, make_header
from email.message import EmailMessage

from .. import secrets
from .base import Connector, spec

IMAP_HOST, SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
SECRET = "gmail-app-password"
BODY_CAP = 8000


def _header(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return str(value)


def _text(message) -> str:
    plain, rich = None, None
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        if part.get_content_type() == "text/plain" and plain is None:
            plain = text
        elif part.get_content_type() == "text/html" and rich is None:
            rich = text
    if plain is None and rich is not None:
        rich = re.sub(r"(?is)<(script|style).*?</\1>", " ", rich)
        plain = html.unescape(re.sub(r"<[^>]+>", " ", rich))
    return re.sub(r"[ \t]+", " ", (plain or "")).strip()[:BODY_CAP]


class GmailConnector(Connector):
    id = "gmail"
    name = "Gmail"
    description = "Search and read your mail, draft replies, send (IMAP/SMTP with a Google app password)."
    ACTIONS = {"draft", "send"}

    imap_factory = staticmethod(imaplib.IMAP4_SSL)
    smtp_factory = staticmethod(smtplib.SMTP_SSL)

    @property
    def address(self) -> str:
        return str(self.config.get("address") or "").strip()

    def status(self) -> dict:
        needs = []
        if not self.address:
            needs.append("address")
        if not secrets.present(SECRET):
            needs.append(SECRET)
        if not needs:
            return {"ok": True, "detail": f"ready for {self.address}", "needs": []}
        bits = []
        if "address" in needs:
            bits.append("your Gmail address")
        if SECRET in needs:
            bits.append("the Gmail app password")
        return {"ok": False, "needs": needs,
                "detail": f"Needs {' and '.join(bits)} (Connectors > Gmail). Google requires 2-step verification for app passwords."}

    def tools(self) -> list[dict]:
        return [
            spec("search", "Search mail with Gmail's own syntax (from:, subject:, newer_than:2d, has:attachment). Returns from, subject, date, uid, snippet.",
                 {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
            spec("read", "Read one message by uid (from search).", {"uid": {"type": "string"}}, ["uid"]),
            spec("draft", "Save a draft in Gmail (does not send). Use reply_to_uid to reply in thread.",
                 {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "reply_to_uid": {"type": "string"}},
                 ["to", "subject", "body"]),
            spec("send", "Send an email now.", {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
                                                "reply_to_uid": {"type": "string"}}, ["to", "subject", "body"]),
        ]

    def _imap(self):
        password = secrets.get(SECRET)
        if not self.address or not password:
            raise ValueError(self.status()["detail"])
        box = self.imap_factory(IMAP_HOST)
        box.login(self.address, password.replace(" ", ""))
        return box

    def _fetch(self, box, uid: str):
        status, data = box.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1])

    def tool_search(self, query: str, limit: int = 20):
        box = self._imap()
        try:
            box.select('"[Gmail]/All Mail"', readonly=True)
            status, data = box.uid("SEARCH", "X-GM-RAW", '"' + str(query).replace('"', "") + '"')
            uids = (data[0] or b"").split() if status == "OK" and data else []
            out = []
            for uid in list(reversed(uids))[: max(1, min(int(limit or 20), 50))]:
                status, meta = box.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT]<0.400>)")
                if status != "OK" or not meta:
                    continue
                blocks = [m[1] for m in meta if isinstance(m, tuple)]
                headers = email.message_from_bytes(blocks[0]) if blocks else {}
                snippet = blocks[1].decode("utf-8", errors="replace") if len(blocks) > 1 else ""
                out.append({"uid": uid.decode(), "from": _header(headers.get("From")), "subject": _header(headers.get("Subject")),
                            "date": headers.get("Date", ""), "snippet": " ".join(re.sub(r"<[^>]+>", " ", snippet).split())[:240]})
            return out or "No mail matches."
        finally:
            try:
                box.logout()
            except Exception:
                pass

    def tool_read(self, uid: str):
        box = self._imap()
        try:
            box.select('"[Gmail]/All Mail"', readonly=True)
            message = self._fetch(box, uid)
            if message is None:
                return f"No message {uid}."
            return {"uid": str(uid), "from": _header(message.get("From")), "to": _header(message.get("To")),
                    "subject": _header(message.get("Subject")), "date": message.get("Date", ""),
                    "message_id": message.get("Message-ID", ""), "body": _text(message)}
        finally:
            try:
                box.logout()
            except Exception:
                pass

    def _compose(self, to: str, subject: str, body: str, reply_to_uid: str | None) -> EmailMessage:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.address, to, subject
        message["Date"] = email.utils.formatdate(localtime=True)
        if reply_to_uid:
            box = self._imap()
            try:
                box.select('"[Gmail]/All Mail"', readonly=True)
                original = self._fetch(box, reply_to_uid)
            finally:
                try:
                    box.logout()
                except Exception:
                    pass
            if original is not None and original.get("Message-ID"):
                message["In-Reply-To"] = original["Message-ID"]
                message["References"] = original["Message-ID"]
        message.set_content(str(body).replace("\u2014", ", "))
        return message

    def tool_draft(self, to: str, subject: str, body: str, reply_to_uid: str | None = None):
        message = self._compose(to, subject, body, reply_to_uid)
        box = self._imap()
        try:
            status, _ = box.append('"[Gmail]/Drafts"', "\\Draft", imaplib.Time2Internaldate(time.time()), message.as_bytes())
        finally:
            try:
                box.logout()
            except Exception:
                pass
        return f"Draft saved for {to}: {subject}" if status == "OK" else "Gmail refused the draft."

    def tool_send(self, to: str, subject: str, body: str, reply_to_uid: str | None = None):
        message = self._compose(to, subject, body, reply_to_uid)
        password = secrets.get(SECRET) or ""
        server = self.smtp_factory(SMTP_HOST, 465)
        try:
            server.login(self.address, password.replace(" ", ""))
            server.send_message(message)
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return f"Sent to {to}: {subject}"
