from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import settings


def send_email(
    *,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    if not settings.smtp_from_email:
        raise RuntimeError(
            "NERDO_SMTP_FROM_EMAIL is not configured."
        )

    message = EmailMessage()
    message["From"] = formataddr(
        (settings.smtp_from_name, settings.smtp_from_email)
    )
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    if settings.smtp_tls_mode == "ssl":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=30,
            context=ssl.create_default_context(),
        )
    else:
        client = smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=30,
        )

    with client:
        client.ehlo()
        if settings.smtp_tls_mode == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()

        if settings.smtp_username:
            client.login(
                settings.smtp_username,
                settings.smtp_password,
            )

        client.send_message(message)
