from __future__ import annotations

import logging
import time

from .config import settings
from .maildir import LocalMaildirSource, MailLedger
from .processor import DomainCommands, parse_command, send_reply

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
    processed = 0

    for item in source.pending():
        try:
            parsed = parse_command(item.message)
            if not parsed.sender:
                raise RuntimeError("Message has no usable From address.")
            result, domain = commands.execute(parsed)
            send_reply(settings, item.message, result, domain)
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
