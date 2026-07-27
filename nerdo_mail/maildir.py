from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path


@dataclass(frozen=True)
class MailItem:
    path: Path
    digest: str
    message: EmailMessage


class MailLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_mail (
                    digest TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def contains(self, digest: str) -> bool:
        with sqlite3.connect(self.path) as conn:
            return conn.execute(
                "SELECT 1 FROM processed_mail WHERE digest = ?", (digest,)
            ).fetchone() is not None

    def claim(self, item: MailItem) -> bool:
        """Reserve a message before any command or outbound reply is executed."""
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO processed_mail (digest, path, status, error)
                VALUES (?, ?, 'processing', NULL)
                """,
                (item.digest, str(item.path)),
            )
            return cursor.rowcount == 1

    def record(self, item: MailItem, status: str, error: str | None = None) -> None:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                """
                UPDATE processed_mail
                SET path = ?, status = ?, error = ?, processed_at = CURRENT_TIMESTAMP
                WHERE digest = ?
                """,
                (str(item.path), status, error, item.digest),
            )
            if cursor.rowcount == 0:
                conn.execute(
                    """
                    INSERT INTO processed_mail (digest, path, status, error)
                    VALUES (?, ?, ?, ?)
                    """,
                    (item.digest, str(item.path), status, error),
                )


class LocalMaildirSource:
    def __init__(self, inbox: Path, ledger: MailLedger, stable_seconds: float = 2):
        self.inbox = inbox
        self.ledger = ledger
        self.stable_seconds = stable_seconds

    def pending(self) -> list[MailItem]:
        if not self.inbox.is_dir():
            return []
        items: list[MailItem] = []
        now = time.time()
        for path in sorted(self.inbox.glob("*.eml")):
            stat = path.stat()
            if now - stat.st_mtime < self.stable_seconds:
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if self.ledger.contains(digest):
                continue
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            items.append(MailItem(path=path, digest=digest, message=parsed))
        return items
