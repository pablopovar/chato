from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from .processor import CommandResult, DomainCommands, ParsedCommand


ADD_DOMAIN_COMMAND_RE = re.compile(
    r"^add\s+domain\s+([^\s|]+)\s+([^\s|]+)\s*$",
    re.I,
)
ADD_DOMAIN_USAGE = "Usage: add domain example.com owner@example.com"


class MailboxDomainCommands(DomainCommands):
    """Mailbox syntax adapter over the channel-agnostic domain operations API."""

    def __init__(self, settings: Any) -> None:
        # Deliberately do not initialize DomainCommands.storage. The mailbox
        # parses and authorizes commands, then calls the domain operations API.
        self.settings = settings

    @property
    def _gateway_headers(self) -> dict[str, str]:
        return {"X-Nerdo-Key": self.settings.operator_token}

    def _gateway_request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = httpx.request(
            method,
            self.settings.gateway_base_url.rstrip("/") + path,
            headers=self._gateway_headers,
            timeout=120,
            **kwargs,
        )
        try:
            payload: Any = response.json()
        except ValueError:
            payload = {"detail": response.text or response.reason_phrase}
        if response.is_error:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise RuntimeError(detail or response.reason_phrase)
        if not isinstance(payload, dict):
            raise RuntimeError("Nerdo API returned an invalid response.")
        return payload

    def _records(self) -> list[dict[str, Any]]:
        payload = self._gateway_request("GET", "/v1/admin/domains")
        rows = payload.get("domains", [])
        return rows if isinstance(rows, list) else []

    def _authorized_records(self, sender: str) -> list[dict[str, Any]]:
        rows = self._records()
        if sender in self.settings.admin_emails:
            return rows
        return [
            row
            for row in rows
            if str(row.get("email", "")).casefold() == sender.casefold()
        ]

    def _authorized_domains(self, sender: str) -> set[str]:
        return {
            str(row.get("domain", "")).casefold().rstrip(".")
            for row in self._authorized_records(sender)
            if row.get("domain")
        }

    def list_domains(self, sender: str) -> str:
        rows = self._authorized_records(sender)
        if not rows:
            return "No domains are available for this sender."
        return "Domains:\n" + "\n".join(
            f"- {row['domain']}: {row.get('status', 'unknown')}"
            for row in sorted(rows, key=lambda item: str(item.get("domain", "")))
        )

    def status(self, domain: str) -> str:
        row = self._gateway_request("GET", f"/v1/admin/domains/{domain}")
        return (
            f"Domain: {row['domain']}\n"
            f"Status: {row.get('status', 'unknown')}\n"
            f"Documents: {row.get('document_count', 0)}\n"
            f"Updated: {row.get('updated_at', '')}"
        )

    def add_domain(self, sender: str, domain: str, email: str) -> str:
        if sender not in self.settings.admin_emails:
            return "Only a configured Nerdo administrator may add a domain."
        created = self._gateway_request(
            "POST",
            "/v1/admin/domains",
            json={"domain": domain, "email": email},
        )
        return (
            f"Added and started {created['domain']} for {created['email']}. "
            f"Intake: {created['intake_id']}. "
            f"Status: {created['status']}."
        )

    def start(self, domain: str) -> str:
        created = self._gateway_request(
            "POST",
            f"/v1/admin/domains/{domain}/start",
        )
        return f"Approved and started {domain}. Intake: {created['intake_id']}."

    def activate(self, domain: str) -> str:
        result = self._gateway_request(
            "POST",
            f"/v1/admin/domains/{domain}/activate",
        )
        if result.get("already_active"):
            return f"{domain} is already active."
        return f"Activated {domain}."

    def retry(self, domain: str) -> str:
        self._gateway_request("POST", f"/v1/admin/domains/{domain}/retry")
        return f"Queued {domain} for processing."

    def set_enabled(self, domain: str, enabled: bool) -> str:
        self._gateway_request(
            "PUT",
            f"/v1/admin/domains/{domain}/enabled",
            json={"enabled": enabled},
        )
        return f"{'Enabled' if enabled else 'Disabled'} {domain}."

    def remove(self, domain: str, confirmed: bool) -> str:
        if not confirmed:
            return (
                "Removal archives the deployed domain. Reply with: "
                f"`Command: confirm remove {domain}`."
            )
        result = self._gateway_request(
            "POST",
            f"/v1/admin/domains/{domain}/remove",
            json={"confirm": True},
        )
        archived = ", ".join(result.get("archived_paths", [])) or "no deployed directory"
        return f"Removed {domain} from service. Archived: {archived}."

    def reset(self, domain: str, confirmed: bool) -> str:
        if not confirmed:
            return (
                "Reset archives the deployed domain, clears its intake and conversation state, "
                "and starts a new crawl. Reply with: "
                f"`Command: confirm reset {domain}`."
            )
        result = self._gateway_request(
            "POST",
            f"/v1/admin/domains/{domain}/reset",
            json={"confirm": True},
        )
        return (
            f"Reset and restarted {domain}. "
            f"Intake: {result['intake_id']}. "
            f"Status: {result['status']}."
        )

    def list_documents(self, domain: str) -> str:
        result = self._gateway_request(
            "GET",
            f"/v1/admin/domains/{domain}/documents",
        )
        documents = result.get("documents", [])
        if not documents:
            return f"No Markdown documents were found for {domain}."
        return (
            f"Documents for {domain} ({len(documents)}):\n"
            + "\n".join(f"- {item['path']}" for item in documents)
        )

    def attach_documents(
        self,
        domain: str,
    ) -> tuple[str, tuple[tuple[str, bytes], ...]]:
        result = self._gateway_request(
            "GET",
            f"/v1/admin/domains/{domain}/documents/export",
        )
        attachments = tuple(
            (
                str(item["filename"]),
                base64.b64decode(str(item["content_base64"]), validate=True),
            )
            for item in result.get("files", [])
        )
        body = f"Attached {len(attachments)} Markdown document(s) for {domain}."
        omitted = int(result.get("omitted") or 0)
        if omitted:
            body += f"\nOmitted {omitted} document(s) because of the attachment limit."
        return body, attachments

    def add_documents(
        self,
        domain: str,
        attachments: tuple[tuple[str, bytes], ...],
    ) -> str:
        if not attachments:
            return (
                "No Markdown attachments were found. "
                "Attach `.md` or `.markdown` files."
            )
        result = self._gateway_request(
            "POST",
            f"/v1/admin/domains/{domain}/documents",
            json={
                "files": [
                    {
                        "filename": filename,
                        "content_base64": base64.b64encode(raw).decode("ascii"),
                    }
                    for filename, raw in attachments
                ]
            },
        )
        documents = result.get("documents", [])
        return (
            f"Added {len(documents)} Markdown document(s) to {domain}: "
            + ", ".join(str(item) for item in documents)
        )

    def execute(self, parsed: ParsedCommand) -> CommandResult:
        normalized = " ".join(parsed.command.split())

        if normalized.casefold().startswith("add domain"):
            match = ADD_DOMAIN_COMMAND_RE.fullmatch(normalized)
            if not match:
                return CommandResult(ADD_DOMAIN_USAGE)
            domain = match.group(1).casefold().rstrip(".")
            email = match.group(2).casefold()
            return CommandResult(
                self.add_domain(parsed.sender, domain, email),
                domain,
            )

        command_domain = self._command_domain(normalized)
        domain, error = self._resolve_domain(parsed, command_domain)
        if normalized.startswith("confirm reset"):
            if error or not domain:
                return CommandResult(error or "A domain is required.")
            return CommandResult(self.reset(domain, True), domain)
        if normalized.startswith("reset"):
            if error or not domain:
                return CommandResult(error or "A domain is required.")
            return CommandResult(self.reset(domain, False), domain)

        result = super().execute(parsed)
        if result.body.startswith("Unknown command."):
            return CommandResult(
                "Unknown command. Supported commands: add domain, list domains, "
                "status, start, activate, retry, reset, enable, disable, add documents, "
                "list documents, attach documents, remove.",
                result.domain,
                result.attachments,
            )
        return result
