from nerdo_mail.command_text import normalize_command_text


def test_preserves_internal_add_domain_delimiter() -> None:
    assert (
        normalize_command_text(
            "|add domain www.arroba-cba.com.ar|pablo@povarchik.com|"
        )
        == "add domain www.arroba-cba.com.ar|pablo@povarchik.com"
    )


def test_plain_command_is_unchanged() -> None:
    assert normalize_command_text("list documents example.com") == "list documents example.com"
