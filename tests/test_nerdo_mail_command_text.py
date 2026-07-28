from nerdo_mail.command_text import normalize_command_text


def test_strips_outer_email_editor_bars() -> None:
    assert normalize_command_text("|status example.com|") == "status example.com"


def test_plain_command_is_unchanged() -> None:
    assert normalize_command_text("list documents example.com") == "list documents example.com"
