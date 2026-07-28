from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from app.config import settings


MAX_SYSTEM_PROMPT_CHARS = 20_000


def review_configuration_path(intake_id: str) -> Path:
    return settings.data_dir / "intakes" / intake_id / "review-configuration.json"


def default_review_configuration(intake: dict[str, Any]) -> dict[str, Any]:
    name = str(
        intake.get("business_name")
        or intake.get("domain")
        or "this website"
    ).strip()
    return {
        "model": settings.model_name,
        "system_prompt": (
            f"You are the website guide for {name}. "
            "Answer from the supplied knowledge. "
            "Do not invent missing information."
        ),
        "temperature": 0.1,
        "max_tokens": 900,
        "max_results": 6,
        "max_context_chars": 18_000,
        "debug": False,
    }


def _bounded_int(
    payload: dict[str, Any],
    key: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(payload.get(key))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}.")
    return value


def validate_review_configuration(
    intake: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    defaults = default_review_configuration(intake)
    merged = {**defaults, **payload}

    model = str(merged.get("model") or "").strip()
    if not model or len(model) > 300:
        raise ValueError("model is required and must be 300 characters or fewer.")

    system_prompt = str(merged.get("system_prompt") or "").strip()
    if not system_prompt:
        raise ValueError("system_prompt is required.")
    if len(system_prompt) > MAX_SYSTEM_PROMPT_CHARS:
        raise ValueError(
            f"system_prompt must be {MAX_SYSTEM_PROMPT_CHARS:,} characters or fewer."
        )

    try:
        temperature = float(merged.get("temperature"))
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be numeric.") from exc
    if not 0.0 <= temperature <= 2.0:
        raise ValueError("temperature must be between 0 and 2.")

    return {
        "model": model,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": _bounded_int(merged, "max_tokens", 64, 8192),
        "max_results": _bounded_int(merged, "max_results", 1, 12),
        "max_context_chars": _bounded_int(
            merged,
            "max_context_chars",
            2_000,
            100_000,
        ),
        "debug": bool(merged.get("debug", False)),
    }


def load_review_configuration(intake: dict[str, Any]) -> dict[str, Any]:
    path = review_configuration_path(str(intake["id"]))
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Could not read review configuration: {exc}") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("The review configuration is not a JSON object.")
        payload = decoded
    return validate_review_configuration(intake, payload)


def save_review_configuration(
    intake: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    configuration = validate_review_configuration(intake, payload)
    path = review_configuration_path(str(intake["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(configuration, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return configuration


def available_models(
    configuration: dict[str, Any],
) -> tuple[list[str], str | None]:
    current = str(configuration.get("model") or "").strip()
    models: set[str] = {current} if current else set()
    headers: dict[str, str] = {}
    if settings.model_api_key:
        headers["Authorization"] = f"Bearer {settings.model_api_key}"
    try:
        response = httpx.get(
            settings.model_base_url.rstrip("/") + "/models",
            headers=headers,
            timeout=min(30.0, settings.model_timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("data", []) if isinstance(payload, dict) else []
        for item in records:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                models.add(str(item["id"]).strip())
    except Exception as exc:
        return sorted(models), f"Could not list models: {exc}"
    return sorted(models), None
