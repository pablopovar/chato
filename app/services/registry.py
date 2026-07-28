from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$"
)


@dataclass(frozen=True)
class BotConfig:
    domain: str
    directory: Path
    enabled: bool
    debug: bool
    key: str
    name: str
    system_prompt: str
    model: str
    model_base_url: str
    model_api_key: str
    allowed_origins: tuple[str, ...]
    max_results: int
    max_context_chars: int
    temperature: float
    max_tokens: int
    welcome_message: str
    suggested_questions: tuple[str, ...]


def normalize_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    domain = domain.encode("idna").decode("ascii")
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise ValueError("Invalid domain.")
    return domain


def _bounded_int(
    raw: dict[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = int(raw.get(key, default))
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{key} must be between {minimum} and {maximum}."
        )
    return value


def find_config_path(domain: str) -> Path | None:
    normalized = normalize_domain(domain)
    candidates = sorted(
        settings.users_dir.glob(f"*/{normalized}/nerdo.json")
    )
    return candidates[0] if candidates else None


def load_bot(domain: str) -> BotConfig | None:
    path = find_config_path(domain)
    if not path:
        return None

    raw = json.loads(path.read_text(encoding="utf-8"))
    directory = path.parent.resolve()
    configured_domain = normalize_domain(
        str(raw.get("domain", directory.name))
    )
    if configured_domain != normalize_domain(directory.name):
        raise RuntimeError(
            f"{path}: domain must match directory name."
        )

    key = str(raw.get("key", "")).strip()
    system_prompt = str(raw.get("system_prompt", "")).strip()
    model = str(raw.get("model", settings.model_name)).strip()
    if len(key) < 8 or not system_prompt or not model:
        raise RuntimeError(f"{path}: incomplete bot configuration.")

    return BotConfig(
        domain=configured_domain,
        directory=directory,
        enabled=bool(raw.get("enabled", True)),
        debug=bool(raw.get("debug", False)),
        key=key,
        name=str(raw.get("name", configured_domain)).strip(),
        system_prompt=system_prompt,
        model=model,
        model_base_url=str(
            raw.get("model_base_url", settings.model_base_url)
        ).rstrip("/"),
        model_api_key=str(
            raw.get("model_api_key", settings.model_api_key)
        ),
        allowed_origins=tuple(
            str(origin).strip().rstrip("/")
            for origin in raw.get("allowed_origins", [])
            if str(origin).strip()
        ),
        max_results=_bounded_int(raw, "max_results", 6, 1, 12),
        max_context_chars=_bounded_int(
            raw,
            "max_context_chars",
            18000,
            2000,
            100000,
        ),
        temperature=float(raw.get("temperature", 0.1)),
        max_tokens=_bounded_int(raw, "max_tokens", 900, 64, 8192),
        welcome_message=str(
            raw.get(
                "welcome_message",
                "Ask a question about this website.",
            )
        ),
        suggested_questions=tuple(
            str(item)
            for item in raw.get("suggested_questions", [])
            if str(item).strip()
        ),
    )


def all_allowed_origins() -> set[str]:
    origins = set(settings.public_origins)
    for path in settings.users_dir.glob("*/*/nerdo.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        origins.update(
            str(origin).strip().rstrip("/")
            for origin in raw.get("allowed_origins", [])
            if str(origin).strip()
        )
    return origins
