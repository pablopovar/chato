from __future__ import annotations

import hashlib
import json
import os
import re
from email.message import EmailMessage
from pathlib import Path

import httpx

from .config import MailSettings
from .processor import CommandResult, ParsedCommand

QUOTED_REPLY_RE = re.compile(r"^on\s+.+\s+wrote:\s*$", re.I)


def question_from_message(parsed: ParsedCommand) -> str:
    """Return the first authored line while excluding quoted reply history."""
    for line in parsed.body.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(">") or QUOTED_REPLY_RE.match(text):
            break
        if re.match(r"^(domain|command)\s*:", text, re.I):
            continue
        text = text.strip("|").strip()
        if text:
            return text
    return parsed.command.strip("|").strip()


def email_session_id(message: EmailMessage, sender: str, domain: str) -> str:
    references = str(message.get("References", "")).split()
    thread_anchor = (
        references[0]
        if references
        else str(message.get("In-Reply-To") or message.get("Message-ID") or message.get("Subject") or "")
    )
    raw = f"{sender.casefold()}\n{domain.casefold()}\n{thread_anchor}".encode("utf-8")
    return "mail-" + hashlib.sha256(raw).hexdigest()[:48]


def _config_path(settings: MailSettings, domain: str) -> Path | None:
    paths = sorted(settings.users_dir.glob(f"*/{domain}/nerdo.json"))
    return paths[0] if paths else None


def answer_email_question(
    settings: MailSettings,
    parsed: ParsedCommand,
    message: EmailMessage,
    domain: str,
) -> CommandResult:
    config_path = _config_path(settings, domain)
    if not config_path:
        return CommandResult(
            f"{domain} is not active yet. Activate it before asking questions by email.",
            domain,
        )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("enabled", True):
        return CommandResult(f"{domain} is currently disabled.", domain)

    key = str(config.get("key") or "").strip()
    if not key:
        return CommandResult(
            f"The active configuration for {domain} has no bot key.",
            domain,
        )

    question = question_from_message(parsed)
    if len(question) < 2:
        return CommandResult("Send a question with at least two characters.", domain)

    timeout = float(
        os.getenv(
            "NERDO_MAIL_CHAT_TIMEOUT_SECONDS",
            os.getenv("NERDO_MODEL_TIMEOUT_SECONDS", "600"),
        )
    )
    response = httpx.post(
        settings.core_base_url + "/chat",
        json={
            "domain": domain,
            "key": key,
            "question": question,
            "session_id": email_session_id(message, parsed.sender, domain),
        },
        timeout=timeout,
    )
    if response.status_code == 404:
        return CommandResult(f"No enabled Chato configuration was found for {domain}.", domain)
    if response.status_code == 401:
        return CommandResult(f"The stored bot key for {domain} was rejected.", domain)
    response.raise_for_status()

    payload = response.json()
    answer = str(payload.get("answer") or payload.get("message") or "").strip()
    if not answer:
        answer = f"Chato returned no answer for {domain}."

    sources = payload.get("sources") or []
    source_lines: list[str] = []
    for source in sources[:5]:
        title = str(source.get("title") or source.get("path") or "Source").strip()
        path = str(source.get("path") or "").strip()
        source_lines.append(f"- {title}" + (f" ({path})" if path and path != title else ""))
    if source_lines:
        answer += "\n\nSources:\n" + "\n".join(source_lines)

    return CommandResult(answer, domain)
