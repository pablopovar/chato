from __future__ import annotations

import logging
import os
import time
from dataclasses import replace

from .command_text import normalize_command_text
from .config import settings
from .maildir import LocalMaildirSource, MailLedger
from .processor import DomainCommands, parse_command, send_reply
from .review_ready import ReviewReadyNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nerdo-mail")


def run_once() -> int:
    ledger = MailLedger(settings.state_path)
    source = LocalMaildirSource(
        settings.inbox_path,
        ledger,
        stable_seconds=settings.stable_seconds,
    )
    commands = DomainCommands(settings)
    notifier = ReviewReadyNotifier(settings)
    processed = 0
    own_address = os.getenv("NERDO_SMTP_FROM_EMAIL", "").strip().casefold()

    try:
        notifier.notify_pending()
    except Exception:
        logger.exception("Review-ready notification scan failed.")

    for item in source.pending():
        if not ledger.claim(item):
            continue
        try:
            parsed = parse_command(item.message)
            parsed = replace(
                parsed,
                command=normalize_command_text(parsed.command),
            )
            if not parsed.sender:
                raise RuntimeError("Message has no usable From address.")
            auto_submitted = str(item.message.get("Auto-Submitted", "no")).casefold()
            if parsed.sender == own_address or auto_submitted not in {"", "no"}:
                ledger.record(item, "ignored")
                logger.info("Ignored automated message %s", item.path.name)
                continue
            result = commands.execute(parsed)
            send_reply(settings, item.message, result)
            ledger.record(item, "processed")
            processed += 1
            logger.info("Processed %s from %s", item.path.name, parsed.sender)
        except Exception as exc:
            ledger.record(item, "failed", str(exc)[:4000])
            logger.exception("Failed processing %s", item.path)
    return processed


def main() -> None:
    logger.info("Watching %s", settings.inbox_path)
    while True:
        run_once()
        time.sleep(max(1.0, settings.poll_seconds))


if __name__ == "__main__":
    main()
