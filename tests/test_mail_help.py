from __future__ import annotations

import unittest
from types import SimpleNamespace

from nerdo_mail.command_dispatch import MailboxDomainCommands, admin_help_text
from nerdo_mail.processor import ParsedCommand


class MailHelpTests(unittest.TestCase):
    def parsed(self, sender: str) -> ParsedCommand:
        return ParsedCommand(
            sender=sender,
            subject="",
            body="help",
            command="help",
            domain=None,
            attachments=(),
        )

    def test_admin_help_returns_full_mailbox_help_without_domain_lookup(self) -> None:
        commands = MailboxDomainCommands(
            SimpleNamespace(admin_emails=frozenset({"admin@example.com"}))
        )
        result = commands.execute(self.parsed("admin@example.com"))

        self.assertEqual(result.body, admin_help_text())
        self.assertIn("add domain DOMAIN EMAIL", result.body)
        self.assertIn("reset DOMAIN", result.body)
        self.assertIn("confirm remove DOMAIN", result.body)
        self.assertIn("Queries:", result.body)
        self.assertIsNone(result.domain)

    def test_non_admin_help_does_not_expose_command_list(self) -> None:
        commands = MailboxDomainCommands(
            SimpleNamespace(admin_emails=frozenset({"admin@example.com"}))
        )
        result = commands.execute(self.parsed("visitor@example.com"))

        self.assertIn("only from a configured administrator", result.body)
        self.assertNotIn("add domain", result.body)


if __name__ == "__main__":
    unittest.main()
