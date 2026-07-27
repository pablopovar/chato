from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw is None else float(raw)


@dataclass(frozen=True)
class Settings:
    core_base_url: str = os.getenv("NERDO_CORE_BASE_URL", "http://nerdo:3401")
    core_admin_token: str = os.getenv("NERDO_CORE_ADMIN_TOKEN", "")
    operator_token: str = os.getenv("NERDO_OPERATOR_TOKEN", "change-me")
    database_path: Path = Path(os.getenv("NERDO_DATABASE_PATH", "./data/chato-nerdo.sqlite3"))
    request_timeout_seconds: float = _float("NERDO_REQUEST_TIMEOUT_SECONDS", 30.0)
    verify_timeout_seconds: float = _float("NERDO_VERIFY_TIMEOUT_SECONDS", 12.0)
    public_base_url: str = os.getenv("NERDO_PUBLIC_BASE_URL", "https://chato.povarchik.com")
    widget_script_url: str = os.getenv("NERDO_WIDGET_SCRIPT_URL", "")


settings = Settings()
