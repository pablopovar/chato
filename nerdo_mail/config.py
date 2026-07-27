from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _emails(name: str) -> frozenset[str]:
    return frozenset(
        item.strip().casefold()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class MailSettings:
    mailbox_root: Path = Path(os.getenv("NERDO_MAILDIR_ROOT", "/mailbox"))
    inbox_relative: Path = Path(os.getenv("NERDO_MAILDIR_INBOX", "INBOX/cur"))
    state_path: Path = Path(os.getenv("NERDO_MAIL_STATE_PATH", "/app/data/nerdo-mail.sqlite3"))
    gateway_database_path: Path = Path(
        os.getenv("NERDO_DATABASE_PATH", "/app/data/nerdo-api.sqlite3")
    )
    users_dir: Path = Path(os.getenv("NERDO_USERS_DIR", "/app/users"))
    core_base_url: str = os.getenv("NERDO_CORE_BASE_URL", "http://nerdo:3401").rstrip("/")
    core_admin_token: str = os.getenv("NERDO_CORE_ADMIN_TOKEN", "")
    poll_seconds: float = float(os.getenv("NERDO_MAIL_POLL_SECONDS", "10"))
    stable_seconds: float = float(os.getenv("NERDO_MAIL_STABLE_SECONDS", "2"))
    admin_emails: frozenset[str] = _emails("NERDO_ADMIN_EMAILS")

    @property
    def inbox_path(self) -> Path:
        return self.mailbox_root / self.inbox_relative


settings = MailSettings()
