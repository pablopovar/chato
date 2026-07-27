from __future__ import annotations

import json
import os
import re
import shutil
import smtplib
import ssl
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage, Message
from email.utils import getaddresses, parseaddr
from pathlib import Path
from urllib.parse import urlparse

import httpx

from nerdo_api.storage import Storage

from .config import MailSettings

DOMAIN_RE = re.compile(
    r"(?<![a-z0-9-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])",
    re.I,
)
DOMAIN_LINE_RE = re.compile(r"^\s*domain\s*:\s*([^\s]+)\s*$", re.I | re.M)
COMMAND_LINE_RE = re.compile(r"^\s*command\s*:\s*(.+?)\s*$", re.I | re.M)
SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class ParsedCommand:
    sender: str
    subject: str
    body: str
    command: str
    domain: str | None
    attachments: tuple[tuple[str, bytes], ...]


def _plain_body(message: Message) -> str:
    if message.is_multipart():
        parts: list[str] = []
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                try:
                    parts.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
        return "\n".join(parts).strip()
    try:
        return str(message.get_content()).strip()
    except Exception:
        payload = message.get_payload(decode=True) or b""
        return payload.decode(message.get_content_charset() or "utf-8", "replace").strip()


def _attachments(message: Message) -> tuple[tuple[str, bytes], ...]:
    found: list[tuple[str, bytes]] = []
    for part in message.walk():
        name = part.get_filename()
        if not name:
            continue
        if Path(name).suffix.casefold() not in {".md", ".markdown"}:
            continue
        found.append((name, part.get_payload(decode=True) or b""))
    return tuple(found)


def _recipient_domain(message: Message) -> str | None:
    addresses = getaddresses(
        [str(message.get(name, "")) for name in ("To", "Cc", "Delivered-To", "X-Original-To")]
    )
    for _display, address in addresses:
        local = address.split("@", 1)[0]
        if "+" not in local:
            continue
        tag = local.split("+", 1)[1].casefold().strip(".")
        if DOMAIN_RE.fullmatch(tag):
            return tag
    return None


def parse_command(message: EmailMessage) -> ParsedCommand:
    sender = parseaddr(str(message.get("From", "")))[1].casefold()
    subject = str(message.get("Subject", "")).strip()
    body = _plain_body(message)

    domain = _recipient_domain(message)
    if not domain:
        match = DOMAIN_LINE_RE.search(body)
        if match:
            candidate = match.group(1).casefold().strip().rstrip(".")
            if DOMAIN_RE.fullmatch(candidate):
                domain = candidate
    if not domain:
        match = DOMAIN_RE.search(subject)
        if match:
            domain = match.group(0).casefold().rstrip(".")

    command_match = COMMAND_LINE_RE.search(body)
    if command_match:
        command = command_match.group(1).strip()
    elif subject.casefold().startswith("nerdo:"):
        command = subject.split(":", 1)[1].strip()
    else:
        command = next((line.strip() for line in body.splitlines() if line.strip()), subject)

    return ParsedCommand(
        sender=sender,
        subject=subject,
        body=body,
        command=command.casefold().strip(),
        domain=domain,
        attachments=_attachments(message),
    )


class DomainCommands:
    def __init__(self, settings: MailSettings):
        self.settings = settings
        self.storage = Storage(settings.gateway_database_path)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Admin-Token": self.settings.core_admin_token}

    def _core_get(self, path: str) -> dict:
        response = httpx.get(
            self.settings.core_base_url + path,
            headers=self._headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _core_post(self, path: str, *, json_body: dict | None = None) -> dict:
        response = httpx.post(
            self.settings.core_base_url + path,
            headers=self._headers,
            json=json_body,
            timeout=90,
        )
        response.raise_for_status()
        return response.json()

    def _intakes(self) -> list[dict]:
        return self._core_get("/admin/intakes").get("intakes", [])

    def _submissions(self) -> list[dict]:
        if not self.settings.gateway_database_path.exists():
            return []
        with sqlite3.connect(self.settings.gateway_database_path) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_submissions'"
            ).fetchone()
            if not exists:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT * FROM site_submissions ORDER BY created_at DESC, id DESC"
            ).fetchall()]

    def _authorized_domains(self, sender: str) -> set[str]:
        if sender in self.settings.admin_emails:
            return {
                *(str(item.get("domain", "")).casefold() for item in self._intakes()),
                *(str(item.get("domain", "")).casefold() for item in self._submissions()),
            } - {""}
        domains = {
            str(item.get("domain", "")).casefold()
            for item in self._intakes()
            if str(item.get("email", "")).casefold() == sender
        }
        domains.update(
            str(item.get("domain", "")).casefold()
            for item in self._submissions()
            if str(item.get("email", "")).casefold() == sender
        )
        return domains - {""}

    def _resolve_domain(self, parsed: ParsedCommand) -> tuple[str | None, str | None]:
        allowed = sorted(self._authorized_domains(parsed.sender))
        if parsed.domain:
            if parsed.sender not in self.settings.admin_emails and parsed.domain not in allowed:
                return None, f"{parsed.sender} is not authorized for {parsed.domain}."
            return parsed.domain, None
        if len(allowed) == 1:
            return allowed[0], None
        if not allowed:
            return None, "No domain is associated with this sender. Include `Domain: example.com`."
        return None, "More than one domain is associated with this sender. Include `Domain: example.com`."

    def list_domains(self, sender: str) -> str:
        allowed = self._authorized_domains(sender)
        rows: dict[str, str] = {}
        for item in self._submissions():
            domain = str(item.get("domain", "")).casefold()
            if domain in allowed:
                rows.setdefault(domain, str(item.get("status", "unknown")))
        for item in self._intakes():
            domain = str(item.get("domain", "")).casefold()
            if domain in allowed:
                rows[domain] = str(item.get("status", "unknown"))
        if not rows:
            return "No domains are available for this sender."
        return "Domains:\n" + "\n".join(f"- {domain}: {rows[domain]}" for domain in sorted(rows))

    def status(self, domain: str) -> str:
        intake = next((row for row in self._intakes() if row.get("domain") == domain), None)
        if intake:
            return (
                f"Domain: {domain}\n"
                f"Status: {intake.get('status')}\n"
                f"Documents: {intake.get('document_count', 0)}\n"
                f"Updated: {intake.get('updated_at', '')}"
            )
        submission = next((row for row in self._submissions() if row.get("domain") == domain), None)
        if submission:
            return f"Domain: {domain}\nStatus: {submission.get('status')}"
        return f"No record was found for {domain}."

    def start(self, domain: str) -> str:
        submission = next(
            (
                row for row in self._submissions()
                if row.get("domain") == domain and row.get("status") == "pending_approval"
            ),
            None,
        )
        if not submission:
            return f"No pending approval exists for {domain}."
        response = httpx.post(
            self.settings.core_base_url + "/intakes",
            json={
                "website_url": submission["website_url"],
                "email": submission["email"],
                "business_name": submission.get("business_name"),
            },
            timeout=30,
        )
        response.raise_for_status()
        created = response.json()
        site, _site_token = self.storage.create_site(
            website_url=submission["website_url"],
            email=submission["email"],
            business_name=submission.get("business_name"),
            domain=domain,
            intake_id=created["intake_id"],
            core_status_token=created["status_token"],
            status=created["status"],
        )
        with sqlite3.connect(self.settings.gateway_database_path) as conn:
            conn.execute(
                "UPDATE site_submissions SET status='started', site_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (site["id"], submission["id"]),
            )
        return f"Approved and started {domain}. Intake: {created['intake_id']}."

    def activate(self, domain: str) -> str:
        intake = next((row for row in self._intakes() if row.get("domain") == domain), None)
        if not intake:
            return f"No intake was found for {domain}."
        if intake.get("status") == "active":
            return f"{domain} is already active."
        if intake.get("status") != "awaiting_review":
            return f"{domain} cannot be activated while status is {intake.get('status')}."
        self._core_post(f"/admin/intakes/{intake['id']}/activate", json_body={})
        return f"Activated {domain}."

    def retry(self, domain: str) -> str:
        intake = next((row for row in self._intakes() if row.get("domain") == domain), None)
        if not intake:
            return f"No intake was found for {domain}."
        self._core_post(f"/admin/intakes/{intake['id']}/retry")
        return f"Queued {domain} for processing."

    def _config_path(self, domain: str) -> Path | None:
        paths = sorted(self.settings.users_dir.glob(f"*/{domain}/nerdo.json"))
        return paths[0] if paths else None

    def set_enabled(self, domain: str, enabled: bool) -> str:
        path = self._config_path(domain)
        if not path:
            return f"No active domain configuration was found for {domain}."
        data = json.loads(path.read_text(encoding="utf-8"))
        data["enabled"] = enabled
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return f"{'Enabled' if enabled else 'Disabled'} {domain}."

    def remove(self, domain: str, confirmed: bool) -> str:
        path = self._config_path(domain)
        if not path:
            return f"No active domain configuration was found for {domain}."
        if not confirmed:
            return f"Removal archives the deployed domain. Reply with: `Command: confirm remove {domain}`."
        domain_dir = path.parent
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self.settings.users_dir / ".removed" / stamp / domain
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(domain_dir), str(archive))
        return f"Removed {domain} from service and archived it at {archive}."

    def add_documents(self, domain: str, attachments: tuple[tuple[str, bytes], ...]) -> str:
        path = self._config_path(domain)
        if not path:
            return f"No active domain configuration was found for {domain}."
        if not attachments:
            return "No Markdown attachments were found. Attach `.md` or `.markdown` files."
        target = path.parent / "mail-imports"
        target.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for original, raw in attachments:
            name = SAFE_NAME_RE.sub("-", Path(original).name).strip(".-") or "document.md"
            if Path(name).suffix.casefold() not in {".md", ".markdown"}:
                continue
            raw.decode("utf-8")
            destination = target / name
            if destination.exists():
                destination = target / f"{destination.stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{destination.suffix}"
            destination.write_bytes(raw)
            saved.append(destination.name)
        return f"Added {len(saved)} Markdown document(s) to {domain}: " + ", ".join(saved)

    def execute(self, parsed: ParsedCommand) -> tuple[str, str | None]:
        normalized = " ".join(parsed.command.split())
        if normalized in {"list", "list domains", "domains"}:
            return self.list_domains(parsed.sender), None

        domain, error = self._resolve_domain(parsed)
        if error or not domain:
            return error or "A domain is required.", None

        if normalized.startswith("status"):
            return self.status(domain), domain
        if normalized.startswith("start") or normalized.startswith("approve"):
            return self.start(domain), domain
        if normalized.startswith("activate"):
            return self.activate(domain), domain
        if normalized.startswith("retry"):
            return self.retry(domain), domain
        if normalized.startswith("disable"):
            return self.set_enabled(domain, False), domain
        if normalized.startswith("enable"):
            return self.set_enabled(domain, True), domain
        if normalized.startswith("confirm remove"):
            return self.remove(domain, True), domain
        if normalized.startswith("remove"):
            return self.remove(domain, False), domain
        if normalized.startswith("add documents") or normalized.startswith("add document"):
            return self.add_documents(domain, parsed.attachments), domain
        return (
            "Unknown command. Supported commands: list domains, status, start, activate, retry, "
            "enable, disable, add documents, remove.",
            domain,
        )


def send_reply(settings: MailSettings, original: EmailMessage, body: str, domain: str | None) -> None:
    host = os.getenv("NERDO_SMTP_HOST", "").strip()
    from_email = os.getenv("NERDO_SMTP_FROM_EMAIL", "").strip()
    if not host or not from_email:
        raise RuntimeError("NERDO_SMTP_HOST and NERDO_SMTP_FROM_EMAIL are required.")

    destination = parseaddr(str(original.get("Reply-To") or original.get("From") or ""))[1]
    message = EmailMessage()
    from_name = os.getenv("NERDO_SMTP_FROM_NAME", "Chato & Nerdo").strip()
    message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    message["To"] = destination
    subject = str(original.get("Subject", "")).strip()
    message["Subject"] = subject if subject.casefold().startswith("re:") else f"Re: {subject or 'Nerdo'}"
    message_id = original.get("Message-ID")
    if message_id:
        message["In-Reply-To"] = message_id
        references = str(original.get("References", "")).strip()
        message["References"] = f"{references} {message_id}".strip()
    if domain and "@" in from_email:
        local, mail_domain = from_email.split("@", 1)
        message["Reply-To"] = f"{local}+{domain}@{mail_domain}"
    message.set_content(body.rstrip() + "\n")

    port = int(os.getenv("NERDO_SMTP_PORT", "25"))
    mode = os.getenv("NERDO_SMTP_TLS_MODE", "none").casefold()
    timeout = float(os.getenv("NERDO_SMTP_TIMEOUT_SECONDS", "20"))
    username = os.getenv("NERDO_SMTP_USERNAME", "").strip()
    password = os.getenv("NERDO_SMTP_PASSWORD", "")

    if mode == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        client = smtplib.SMTP(host, port, timeout=timeout)
    with client:
        client.ehlo()
        if mode == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        elif mode not in {"none", "ssl"}:
            raise RuntimeError("NERDO_SMTP_TLS_MODE must be none, starttls, or ssl.")
        if username:
            client.login(username, password)
        client.send_message(message)
