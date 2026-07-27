from __future__ import annotations

import tomllib

from app.config import settings


def enabled_demos() -> list[dict]:
    if not settings.demos_path.is_file():
        return []

    raw = tomllib.loads(
        settings.demos_path.read_text(encoding="utf-8")
    )
    demos = raw.get("demos", [])
    return [
        demo
        for demo in demos
        if isinstance(demo, dict) and demo.get("enabled", False)
    ]
