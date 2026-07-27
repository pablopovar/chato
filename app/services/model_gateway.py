from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    selected_base_url = (base_url or settings.model_base_url).rstrip("/")
    selected_api_key = (
        settings.model_api_key if api_key is None else api_key
    )
    selected_model = model or settings.model_name

    headers = {"Content-Type": "application/json"}
    if selected_api_key:
        headers["Authorization"] = f"Bearer {selected_api_key}"

    payload: dict[str, Any] = {
        "model": selected_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or settings.model_max_tokens,
        "stream": False,
    }

    with httpx.Client(
        timeout=timeout_seconds or settings.model_timeout_seconds,
    ) as client:
        response = client.post(
            f"{selected_base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Model response did not contain choices.")

    answer = str(
        choices[0].get("message", {}).get("content", "")
    ).strip()
    if not answer:
        raise RuntimeError("Model returned an empty response.")

    return answer
