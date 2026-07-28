from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.services.chat_trace import current_trace


_REASONING_EFFORTS = {"none", "low", "medium", "high"}


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""

    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif isinstance(text, dict) and isinstance(text.get("value"), str):
            parts.append(str(text["value"]))
    return "".join(parts).strip()


def _empty_response_error(data: dict[str, Any], choice: dict[str, Any]) -> RuntimeError:
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage.get("completion_tokens_details"), dict)
        else {}
    )

    details: list[str] = []
    finish_reason = str(choice.get("finish_reason") or "").strip()
    if finish_reason:
        details.append(f"finish_reason={finish_reason}")

    completion_tokens = usage.get("completion_tokens")
    if isinstance(completion_tokens, int):
        details.append(f"completion_tokens={completion_tokens}")

    reasoning_tokens = completion_details.get("reasoning_tokens")
    if isinstance(reasoning_tokens, int):
        details.append(f"reasoning_tokens={reasoning_tokens}")

    reasoning_present = any(
        bool(message.get(field))
        for field in ("reasoning", "reasoning_content", "thinking")
    )
    if reasoning_present:
        details.append("reasoning_present=true")

    suffix = f" ({', '.join(details)})" if details else ""
    return RuntimeError(f"Model returned an empty final response{suffix}.")


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float | None = None,
    reasoning_effort: str | None = None,
) -> str:
    selected_base_url = (base_url or settings.model_base_url).rstrip("/")
    selected_api_key = (
        settings.model_api_key if api_key is None else api_key
    )
    selected_model = model or settings.model_name
    selected_timeout = timeout_seconds or settings.model_timeout_seconds

    if reasoning_effort is not None:
        reasoning_effort = reasoning_effort.strip().casefold()
        if reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError(
                "reasoning_effort must be one of: none, low, medium, high."
            )

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
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort

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
        if not isinstance(choices[0], dict):
            raise RuntimeError("Model response contained an invalid choice.")

        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Model response did not contain an assistant message.")

        answer = _message_text(message.get("content"))
        if not answer:
            raise _empty_response_error(data, choice)
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
