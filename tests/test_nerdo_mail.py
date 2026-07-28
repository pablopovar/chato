from email.message import EmailMessage

from nerdo_mail.command_dispatch import (
    ADD_DOMAIN_COMMAND_RE,
    ADD_DOMAIN_USAGE,
    MailboxDomainCommands,
)
from nerdo_mail.processor import DomainCommands, ParsedCommand, parse_command


def test_parses_explicit_domain_and_command() -> None:
    message = EmailMessage()
    message["From"] = "Pablo <pablo@povarchik.com>"
    message["To"] = "nerdo@nerdo.povarchik.com"
    message["Subject"] = "Nerdo request"
    message.set_content("Domain: example.com\nCommand: status\n")

    parsed = parse_command(message)

    assert parsed.sender == "pablo@povarchik.com"
    assert parsed.domain == "example.com"
    assert parsed.command == "status"


def test_reply_address_resolves_domain() -> None:
    message = EmailMessage()
    message["From"] = "owner@example.net"
    message["To"] = "nerdo+example.com@nerdo.povarchik.com"
    message["Subject"] = "Re: Nerdo"
    message.set_content("Command: add documents\n")

    parsed = parse_command(message)

    assert parsed.domain == "example.com"
    assert parsed.command == "add documents"


def test_collects_only_markdown_attachments() -> None:
    message = EmailMessage()
    message["From"] = "owner@example.net"
    message["To"] = "nerdo@nerdo.povarchik.com"
    message["Subject"] = "Nerdo: add documents example.com"
    message.set_content("Domain: example.com\nCommand: add documents\n")
    message.add_attachment(
        b"# Source\n",
        maintype="text",
        subtype="markdown",
        filename="source.md",
    )
    message.add_attachment(
        b"ignored",
        maintype="application",
        subtype="octet-stream",
        filename="file.bin",
    )

    parsed = parse_command(message)

    assert [name for name, _raw in parsed.attachments] == ["source.md"]


def test_add_domain_command_format_uses_spaces_only() -> None:
    match = ADD_DOMAIN_COMMAND_RE.fullmatch(
        "add domain example.com owner@example.net"
    )

    assert match is not None
    assert match.group(1) == "example.com"
    assert match.group(2) == "owner@example.net"


def test_add_domain_command_rejects_bar_separators() -> None:
    assert (
        ADD_DOMAIN_COMMAND_RE.fullmatch(
            "add domain example.com|owner@example.net"
        )
        is None
    )


def test_mailbox_dispatches_plain_add_domain_command() -> None:
    class FakeCommands(MailboxDomainCommands):
        def __init__(self) -> None:
            pass

        def add_domain(self, sender: str, domain: str, email: str) -> str:
            return f"{sender}:{domain}:{email}"

    parsed = ParsedCommand(
        sender="admin@example.net",
        subject="",
        body="",
        command="add domain example.com owner@example.net",
        domain=None,
        attachments=(),
    )

    result = FakeCommands().execute(parsed)

    assert result.domain == "example.com"
    assert result.body == "admin@example.net:example.com:owner@example.net"


def test_mailbox_returns_usage_for_bar_separated_add_domain() -> None:
    class FakeCommands(MailboxDomainCommands):
        def __init__(self) -> None:
            pass

    parsed = ParsedCommand(
        sender="admin@example.net",
        subject="",
        body="",
        command="add domain example.com|owner@example.net",
        domain=None,
        attachments=(),
    )

    result = FakeCommands().execute(parsed)

    assert result.domain is None
    assert result.body == ADD_DOMAIN_USAGE


def test_reset_command_executes_immediately() -> None:
    class FakeCommands(MailboxDomainCommands):
        def __init__(self) -> None:
            pass

        def _resolve_domain(self, parsed, command_domain):
            return command_domain, None

        def reset(self, domain: str) -> str:
            return f"reset:{domain}"

    parsed = ParsedCommand(
        sender="admin@example.net",
        subject="",
        body="",
        command="reset example.com",
        domain=None,
        attachments=(),
    )

    result = FakeCommands().execute(parsed)

    assert result.domain == "example.com"
    assert result.body == "reset:example.com"


def test_command_domain_is_read_from_document_commands() -> None:
    assert (
        DomainCommands._command_domain("list documents example.com")
        == "example.com"
    )
    assert (
        DomainCommands._command_domain("attach documents example.com")
        == "example.com"
    )
