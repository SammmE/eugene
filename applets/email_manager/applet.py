from __future__ import annotations

import asyncio
import contextlib
import email
import email.policy
import imaplib
import json
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any

from eugene.core import AppletBase, FieldSpec
from eugene.config import DATA_DIR
from eugene.models import ToolDefinition, TriggerDefinition


class EmailManagerApplet(AppletBase):
    name = "email_manager"
    description = "Manage email via IMAP and SMTP."
    load = "lazy"
    inject = "selective"
    _watch_task: asyncio.Task[None] | None

    class Config:
        fields = {
            "imap_host": FieldSpec(default="", description="IMAP server hostname."),
            "imap_port": FieldSpec(default=993, description="IMAP server port."),
            "imap_use_ssl": FieldSpec(default=True, description="Use SSL for IMAP."),
            "imap_user": FieldSpec(default="", description="IMAP username / email address."),
            "imap_password": FieldSpec(default="", description="IMAP password. Set via EMAIL_MANAGER_IMAP_PASSWORD in .env."),
            "smtp_host": FieldSpec(default="", description="SMTP server hostname."),
            "smtp_port": FieldSpec(default=587, description="SMTP server port."),
            "smtp_use_tls": FieldSpec(default=True, description="Use STARTTLS for SMTP."),
            "smtp_user": FieldSpec(default="", description="SMTP username (defaults to imap_user if empty)."),
            "smtp_password": FieldSpec(default="", description="SMTP password. Set via EMAIL_MANAGER_SMTP_PASSWORD in .env."),
            "max_fetch": FieldSpec(default=20, description="Maximum emails returned by fetch_emails."),
            "default_mailbox": FieldSpec(default="INBOX", description="Default IMAP mailbox/folder."),
            "proactive_enabled": FieldSpec(default=True, description="Poll inbox for proactive email trigger signals."),
            "proactive_poll_seconds": FieldSpec(default=120, description="Seconds between lightweight inbox polls."),
            "proactive_max_fetch": FieldSpec(default=10, description="Maximum unseen emails inspected per proactive poll."),
            "urgent_keywords": FieldSpec(default="urgent,asap,immediately,action required,important", description="Comma-separated keywords used for urgency detection."),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._watch_task = None

    async def on_load(self) -> None:
        if self._cfg("proactive_enabled", True) and self._imap_user() and self._imap_password() and self._cfg("imap_host"):
            self._watch_task = asyncio.create_task(self._watch_loop())

    async def on_unload(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None

    # ── helpers ──────────────────────────────────────────────────────────

    def _cfg(self, key: str, fallback: Any = "") -> Any:
        return self.config.get(key, fallback)

    def _imap_user(self) -> str:
        return str(self._cfg("imap_user"))

    def _imap_password(self) -> str:
        return str(self._cfg("imap_password"))

    def _smtp_user(self) -> str:
        v = str(self._cfg("smtp_user"))
        return v if v else self._imap_user()

    def _smtp_password(self) -> str:
        v = str(self._cfg("smtp_password"))
        return v if v else self._imap_password()

    def _connect_imap(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        host = str(self._cfg("imap_host"))
        port = int(self._cfg("imap_port", 993))
        if self._cfg("imap_use_ssl", True):
            conn = imaplib.IMAP4_SSL(host, port)
        else:
            conn = imaplib.IMAP4(host, port)
        conn.login(self._imap_user(), self._imap_password())
        return conn

    def _connect_smtp(self) -> smtplib.SMTP | smtplib.SMTP_SSL:
        host = str(self._cfg("smtp_host"))
        port = int(self._cfg("smtp_port", 587))
        use_tls = self._cfg("smtp_use_tls", True)
        if port == 465:
            server = smtplib.SMTP_SSL(host, port)
        else:
            server = smtplib.SMTP(host, port)
            if use_tls:
                server.starttls()
        server.login(self._smtp_user(), self._smtp_password())
        return server

    @staticmethod
    def _decode_header(raw: str | None) -> str:
        if not raw:
            return ""
        parts = email.header.decode_header(raw)
        decoded: list[str] = []
        for fragment, charset in parts:
            if isinstance(fragment, bytes):
                decoded.append(fragment.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(fragment)
        return " ".join(decoded)

    @staticmethod
    def _body_text(msg: email.message.Message) -> str:
        """Extract plain-text body from a parsed email message."""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
            # fallback: try text/html
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
            return ""
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""

    # ── tool definitions ─────────────────────────────────────────────────

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="fetch_emails",
                description="Fetch email headers from a mailbox. Returns uid, from, to, subject, date, and a body snippet.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mailbox": {"type": "string", "description": "IMAP mailbox (default: INBOX)."},
                        "limit": {"type": "integer", "description": "Max emails to return (default: config max_fetch)."},
                        "unseen_only": {"type": "boolean", "description": "Only return unseen/unread emails."},
                    },
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="read_email",
                description="Fetch the full body of a single email by UID.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Email UID."},
                        "mailbox": {"type": "string", "description": "IMAP mailbox (default: INBOX)."},
                    },
                    "required": ["uid"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="send_email",
                description="Send an email via SMTP.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient address (comma-separated for multiple)."},
                        "subject": {"type": "string"},
                        "body": {"type": "string", "description": "Plain-text email body."},
                        "cc": {"type": "string", "description": "CC addresses (comma-separated)."},
                        "bcc": {"type": "string", "description": "BCC addresses (comma-separated)."},
                    },
                    "required": ["to", "subject", "body"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="create_draft",
                description="Save an email draft to the Drafts folder via IMAP.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
                applet_name=self.name,
            ),
            ToolDefinition(
                name="move_email_to_folder",
                description="Move an email from one IMAP folder to another (copy + delete original).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string", "description": "Email UID."},
                        "source": {"type": "string", "description": "Source folder (default: INBOX)."},
                        "destination": {"type": "string", "description": "Destination folder."},
                    },
                    "required": ["uid", "destination"],
                },
                applet_name=self.name,
            ),
        ]

    def get_trigger_definitions(self) -> list[TriggerDefinition]:
        return [
            TriggerDefinition(
                name="new_email",
                description="Emitted when a new unseen email is detected during background inbox monitoring.",
                applet_name=self.name,
                payload_schema={
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string"},
                        "subject": {"type": "string"},
                        "from": {"type": "string"},
                        "snippet": {"type": "string"},
                    },
                },
            ),
            TriggerDefinition(
                name="urgent_email_detected",
                description="Emitted when a newly seen email matches configured urgency keywords.",
                applet_name=self.name,
                payload_schema={
                    "type": "object",
                    "properties": {
                        "uid": {"type": "string"},
                        "subject": {"type": "string"},
                        "from": {"type": "string"},
                        "snippet": {"type": "string"},
                        "urgency_reason": {"type": "string"},
                    },
                },
            ),
        ]

    # ── tool dispatch ────────────────────────────────────────────────────

    async def handle_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "fetch_emails":
            return await self._fetch_emails(arguments)
        if name == "read_email":
            return await self._read_email(arguments)
        if name == "send_email":
            return await self._send_email(arguments)
        if name == "create_draft":
            return await self._create_draft(arguments)
        if name == "move_email_to_folder":
            return await self._move_email(arguments)
        raise ValueError(name)

    # ── implementations ──────────────────────────────────────────────────

    async def _fetch_emails(self, args: dict[str, Any]) -> list[dict[str, str]]:
        mailbox = args.get("mailbox") or self._cfg("default_mailbox", "INBOX")
        limit = int(args.get("limit") or self._cfg("max_fetch", 20))
        unseen_only: bool = args.get("unseen_only", False)

        def _work() -> list[dict[str, str]]:
            conn = self._connect_imap()
            try:
                conn.select(mailbox, readonly=True)
                criterion = "UNSEEN" if unseen_only else "ALL"
                _status, data = conn.search(None, criterion)
                uids = data[0].split() if data[0] else []
                uids = uids[-limit:]  # most recent N
                results: list[dict[str, str]] = []
                for uid in reversed(uids):
                    _status, msg_data = conn.fetch(uid, "(RFC822.HEADER BODY.PEEK[TEXT]<0.500>)")
                    if not msg_data or msg_data[0] is None:
                        continue
                    header_raw = b""
                    snippet_raw = b""
                    for part in msg_data:
                        if isinstance(part, tuple):
                            desc = part[0].decode("ascii", errors="replace").upper() if isinstance(part[0], bytes) else str(part[0]).upper()
                            if "HEADER" in desc:
                                header_raw = part[1] if isinstance(part[1], bytes) else b""
                            elif "TEXT" in desc or "BODY" in desc:
                                snippet_raw = part[1] if isinstance(part[1], bytes) else b""
                    if not header_raw:
                        for part in msg_data:
                            if isinstance(part, tuple):
                                header_raw = part[1] if isinstance(part[1], bytes) else b""
                                break
                    msg = email.message_from_bytes(header_raw, policy=email.policy.default)
                    snippet = snippet_raw.decode("utf-8", errors="replace")[:500].strip()
                    results.append({
                        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                        "from": self._decode_header(msg.get("From")),
                        "to": self._decode_header(msg.get("To")),
                        "subject": self._decode_header(msg.get("Subject")),
                        "date": msg.get("Date", ""),
                        "snippet": snippet,
                    })
                return results
            finally:
                conn.logout()

        return await asyncio.to_thread(_work)

    async def _read_email(self, args: dict[str, Any]) -> str:
        uid = str(args["uid"])
        mailbox = args.get("mailbox") or self._cfg("default_mailbox", "INBOX")

        def _work() -> str:
            conn = self._connect_imap()
            try:
                conn.select(mailbox, readonly=True)
                _status, data = conn.fetch(uid.encode(), "(RFC822)")
                if not data or data[0] is None:
                    return "Email not found."
                raw = data[0][1] if isinstance(data[0], tuple) else b""
                msg = email.message_from_bytes(raw, policy=email.policy.default)
                body = self._body_text(msg)
                header_block = (
                    f"From: {self._decode_header(msg.get('From'))}\n"
                    f"To: {self._decode_header(msg.get('To'))}\n"
                    f"Subject: {self._decode_header(msg.get('Subject'))}\n"
                    f"Date: {msg.get('Date', '')}\n"
                )
                return f"{header_block}\n{body}"
            finally:
                conn.logout()

        return await asyncio.to_thread(_work)

    async def _send_email(self, args: dict[str, Any]) -> str:
        to = str(args["to"])
        subject = str(args["subject"])
        body = str(args["body"])
        cc = str(args.get("cc") or "")
        bcc = str(args.get("bcc") or "")

        def _work() -> str:
            msg = EmailMessage()
            msg["From"] = self._smtp_user()
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            if cc:
                msg["Cc"] = cc
            msg.set_content(body)

            all_recipients = [addr.strip() for addr in (to + "," + cc + "," + bcc).split(",") if addr.strip()]
            server = self._connect_smtp()
            try:
                server.send_message(msg, to_addrs=all_recipients)
            finally:
                server.quit()
            return f"Email sent to {to}."

        return await asyncio.to_thread(_work)

    async def _create_draft(self, args: dict[str, Any]) -> str:
        to = str(args["to"])
        subject = str(args["subject"])
        body = str(args["body"])

        def _work() -> str:
            msg = EmailMessage()
            msg["From"] = self._smtp_user()
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg.set_content(body)

            conn = self._connect_imap()
            try:
                draft_folder = None
                for candidate in ("[Gmail]/Drafts", "Drafts", "INBOX.Drafts", "Draft"):
                    status, _ = conn.select(candidate)
                    if status == "OK":
                        draft_folder = candidate
                        break
                if not draft_folder:
                    return "Could not find a Drafts folder on this IMAP server."
                conn.append(draft_folder, "\\Draft", None, msg.as_bytes())
                return f"Draft saved to {draft_folder}."
            finally:
                conn.logout()

        return await asyncio.to_thread(_work)

    async def _move_email(self, args: dict[str, Any]) -> str:
        uid = str(args["uid"])
        source = args.get("source") or self._cfg("default_mailbox", "INBOX")
        destination = str(args["destination"])

        def _work() -> str:
            conn = self._connect_imap()
            try:
                conn.select(source)
                result, _ = conn.copy(uid.encode(), destination)
                if result != "OK":
                    return f"Failed to copy email {uid} to {destination}."
                conn.store(uid.encode(), "+FLAGS", "\\Deleted")
                conn.expunge()
                return f"Moved email {uid} from {source} to {destination}."
            finally:
                conn.logout()

        return await asyncio.to_thread(_work)

    async def _watch_loop(self) -> None:
        poll_seconds = max(15, int(self._cfg("proactive_poll_seconds", 120)))
        while True:
            try:
                await self._poll_for_proactive_signals()
            except Exception:
                self.logger.exception("Email proactive watch poll failed")
            await asyncio.sleep(poll_seconds)

    async def _poll_for_proactive_signals(self) -> None:
        mailbox = str(self._cfg("default_mailbox", "INBOX"))
        limit = int(self._cfg("proactive_max_fetch", 10))
        known_uids = self._load_seen_uids()
        emails = await self._fetch_emails({"mailbox": mailbox, "limit": limit, "unseen_only": True})
        updated = False

        for item in reversed(emails):
            uid = str(item.get("uid", ""))
            if not uid or uid in known_uids:
                continue
            payload = {
                "uid": uid,
                "mailbox": mailbox,
                "from": str(item.get("from", "")),
                "to": str(item.get("to", "")),
                "subject": str(item.get("subject", "")),
                "date": str(item.get("date", "")),
                "snippet": str(item.get("snippet", "")),
            }
            await self.emit_trigger("new_email", payload)
            urgency_reason = self._detect_urgency(payload)
            if urgency_reason:
                await self.emit_trigger("urgent_email_detected", {**payload, "urgency_reason": urgency_reason})
            known_uids.add(uid)
            updated = True

        if updated:
            self._save_seen_uids(known_uids)

    def _detect_urgency(self, payload: dict[str, str]) -> str | None:
        haystack = " ".join([payload.get("subject", ""), payload.get("snippet", "")]).lower()
        for keyword in self._urgent_keywords():
            if keyword in haystack:
                return keyword
        return None

    def _urgent_keywords(self) -> list[str]:
        raw = str(self._cfg("urgent_keywords", "urgent,asap,immediately,action required,important"))
        return [item.strip().lower() for item in raw.split(",") if item.strip()]

    def _state_path(self) -> Path:
        state_dir = DATA_DIR / "applet_state"
        state_dir.mkdir(exist_ok=True)
        return state_dir / "email_manager_seen_uids.json"

    def _load_seen_uids(self) -> set[str]:
        path = self._state_path()
        if not path.exists():
            return set()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(raw, list):
            return set()
        return {str(item) for item in raw[-500:]}

    def _save_seen_uids(self, uids: set[str]) -> None:
        trimmed = sorted(uids)[-500:]
        self._state_path().write_text(json.dumps(trimmed, indent=2), encoding="utf-8")
