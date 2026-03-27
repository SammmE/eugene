from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate
from typing import Any

from eugene.core import AppletBase, FieldSpec
from eugene.models import ToolDefinition


class EmailManagerApplet(AppletBase):
    name = "email_manager"
    description = "Manage email via IMAP and SMTP."
    load = "lazy"
    inject = "selective"

    class Config:
        fields = {
            "imap_host": FieldSpec(default="", description="IMAP server hostname (overridden by IMAP_HOST env var)."),
            "imap_port": FieldSpec(default=993, description="IMAP server port."),
            "imap_use_ssl": FieldSpec(default=True, description="Use SSL for IMAP."),
            "smtp_host": FieldSpec(default="", description="SMTP server hostname (overridden by SMTP_HOST env var)."),
            "smtp_port": FieldSpec(default=587, description="SMTP server port."),
            "smtp_use_tls": FieldSpec(default=True, description="Use STARTTLS for SMTP."),
            "max_fetch": FieldSpec(default=20, description="Maximum emails returned by fetch_emails."),
            "default_mailbox": FieldSpec(default="INBOX", description="Default IMAP mailbox/folder."),
        }

    # ── helpers ──────────────────────────────────────────────────────────

    def _imap_host(self) -> str:
        return os.getenv("IMAP_HOST", "") or str(self.config.get("imap_host", ""))

    def _imap_user(self) -> str:
        return os.getenv("IMAP_USER", "")

    def _imap_password(self) -> str:
        return os.getenv("IMAP_PASSWORD", "")

    def _smtp_host(self) -> str:
        return os.getenv("SMTP_HOST", "") or str(self.config.get("smtp_host", ""))

    def _smtp_user(self) -> str:
        return os.getenv("SMTP_USER", "") or self._imap_user()

    def _smtp_password(self) -> str:
        return os.getenv("SMTP_PASSWORD", "") or self._imap_password()

    def _connect_imap(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        host = self._imap_host()
        port = int(self.config.get("imap_port", 993))
        if self.config.get("imap_use_ssl", True):
            conn = imaplib.IMAP4_SSL(host, port)
        else:
            conn = imaplib.IMAP4(host, port)
        conn.login(self._imap_user(), self._imap_password())
        return conn

    def _connect_smtp(self) -> smtplib.SMTP | smtplib.SMTP_SSL:
        host = self._smtp_host()
        port = int(self.config.get("smtp_port", 587))
        use_tls = self.config.get("smtp_use_tls", True)
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
        mailbox = args.get("mailbox") or self.config.get("default_mailbox", "INBOX")
        limit = int(args.get("limit") or self.config.get("max_fetch", 20))
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
                    # msg_data may contain multiple parts
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
                        # fallback: first tuple payload is headers
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
        mailbox = args.get("mailbox") or self.config.get("default_mailbox", "INBOX")

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
                # Try common draft folder names
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
        source = args.get("source") or self.config.get("default_mailbox", "INBOX")
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
