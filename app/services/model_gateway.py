from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.services.chat_trace import current_trace


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
    selected_timeout = timeout_seconds or settings.model_timeout_seconds

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
    endpoint = f"{selected_base_url}/chat/completions"
    trace = current_trace()
    if trace:
        trace.event(
            "model.request",
            endpoint=endpoint,
            api_key_configured=bool(selected_api_key),
            timeout_seconds=selected_timeout,
            payload=payload,
        )

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=selected_timeout) as client:
            response = client.post(
                endpoint,
                headers=headers,
                json=payload,
            )

        raw_text = response.text
        try:
            data: Any = response.json()
        except ValueError:
            data = None

        if trace:
            trace.event(
                "model.response",
                status_code=response.status_code,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                headers=dict(response.headers),
                body=data if data is not None else raw_text,
            )

        response.raise_for_status()
        if not isinstance(data, dict):
            raise RuntimeError("Model response was not a JSON object.")

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Model response did not contain choices.")

        answer = str(
            choices[0].get("message", {}).get("content", "")
        ).strip()
        if not answer:
            raise RuntimeError("Model returned an empty response.")
        return answer
    except Exception as exc:
        if trace:
            trace.exception(
                "model.failure",
                exc,
                endpoint=endpoint,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        raise
