from email.message import EmailMessage

from nerdo_mail.email_chat import email_session_id, question_from_message
from nerdo_mail.processor import ParsedCommand


def parsed(body: str, command: str = "what is example.com about?") -> ParsedCommand:
    return ParsedCommand(
        sender="owner@example.net",
        subject="Re: Your Chato & Nerdo is ready",
        body=body,
        command=command,
        domain="example.com",
        attachments=(),
    )


def test_question_uses_authored_line_not_quoted_history() -> None:
    item = parsed(
        "What is Example.com about?\n\n"
        "On 7/27/26, Chato & Nerdo wrote:\n"
        "> Attached 12 Markdown documents.\n"
    )

    assert question_from_message(item) == "What is Example.com about?"


def test_question_tolerates_outer_pipe_markers() -> None:
    item = parsed("|What is Example.com about?|\n")

    assert question_from_message(item) == "What is Example.com about?"


def test_replies_in_same_thread_share_session() -> None:
    first = EmailMessage()
    first["Message-ID"] = "<reply-1@example.net>"
    first["References"] = "<thread-root@example.net> <prior@example.net>"

    second = EmailMessage()
    second["Message-ID"] = "<reply-2@example.net>"
    second["References"] = "<thread-root@example.net> <later@example.net>"

    assert email_session_id(first, "owner@example.net", "example.com") == email_session_id(
        second,
        "owner@example.net",
        "example.com",
    )


def test_different_domains_use_different_sessions() -> None:
    message = EmailMessage()
    message["References"] = "<thread-root@example.net>"

    assert email_session_id(message, "owner@example.net", "example.com") != email_session_id(
        message,
        "owner@example.net",
        "another.example.com",
    )
