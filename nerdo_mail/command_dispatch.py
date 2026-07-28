from __future__ import annotations

import re

from .processor import CommandResult, DomainCommands, ParsedCommand


ADD_DOMAIN_COMMAND_RE = re.compile(
    r"^add\s+domain\s+([^\s|]+)\s+([^\s|]+)\s*$",
    re.I,
)
ADD_DOMAIN_USAGE = "Usage: add domain example.com owner@example.com"


class MailboxDomainCommands(DomainCommands):
    """Mailbox-facing command syntax layered over the domain operations."""

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

        return super().execute(parsed)
