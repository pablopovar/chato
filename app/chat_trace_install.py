from __future__ import annotations

import secrets
from typing import Any, Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.services.chat_trace import (
    TraceRecorder,
    reset_current_trace,
    set_current_trace,
)
from app.services.registry import BotConfig, load_bot


def _configuration_snapshot(config: BotConfig) -> dict[str, Any]:
    return {
        "domain": config.domain,
        "directory": str(config.directory),
        "enabled": config.enabled,
        "debug": config.debug,
        "key_configured": bool(config.key),
        "name": config.name,
        "system_prompt": config.system_prompt,
        "model": config.model,
        "model_base_url": config.model_base_url,
        "model_api_key_configured": bool(config.model_api_key),
        "allowed_origins": list(config.allowed_origins),
        "max_results": config.max_results,
        "max_context_chars": config.max_context_chars,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "welcome_message": config.welcome_message,
        "suggested_questions": list(config.suggested_questions),
    }


def _copy_with_session(body: Any, session_id: str) -> Any:
    if getattr(body, "session_id", None):
        return body
    if hasattr(body, "model_copy"):
        return body.model_copy(update={"session_id": session_id})
    if hasattr(body, "copy"):
        return body.copy(update={"session_id": session_id})
    setattr(body, "session_id", session_id)
    return body


def _wrapper(original: Callable[..., Any]) -> Callable[..., Any]:
    def traced_chat(body: Any, request: Any, response: Any) -> Any:
        try:
            config = load_bot(str(body.domain))
        except Exception:
            config = None
        if config is None or not config.debug:
            return original(body=body, request=request, response=response)

        session_id = str(getattr(body, "session_id", "") or secrets.token_urlsafe(24))
        body = _copy_with_session(body, session_id)
        recorder = TraceRecorder(
            domain=config.domain,
            session_id=session_id,
            request_id=str(uuid4()),
        )
        token = set_current_trace(recorder)
        recorder.event(
            "request.received",
            request={
                "method": getattr(request, "method", "POST"),
                "path": str(getattr(getattr(request, "url", None), "path", "/chat")),
                "origin": request.headers.get("origin", "") if request else "",
                "user_agent": request.headers.get("user-agent", "") if request else "",
                "client": (
                    getattr(getattr(request, "client", None), "host", None)
                    if request
                    else None
                ),
            },
            question=str(body.question),
            supplied_session_id=bool(getattr(body, "session_id", None)),
            session_id=session_id,
            configuration=_configuration_snapshot(config),
        )

        try:
            result = original(body=body, request=request, response=response)
            payload = result.model_dump() if hasattr(result, "model_dump") else result
            if isinstance(payload, dict) and payload.get("request_id"):
                recorder.request_id = str(payload["request_id"])
            recorder.event("response.returned", response=payload)
            return result
        except Exception as exc:
            recorder.exception("request.failed", exc)
            raise
        finally:
            recorder.close_and_write()
            reset_current_trace(token)

    traced_chat.__name__ = getattr(original, "__name__", "traced_chat")
    traced_chat.__doc__ = getattr(original, "__doc__", None)
    return traced_chat


def install_chat_tracing(app: FastAPI) -> None:
    if getattr(app.state, "chat_tracing_installed", False):
        return

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != "/chat" or "POST" not in (route.methods or set()):
            continue
        original = route.dependant.call
        wrapped = _wrapper(original)
        route.endpoint = wrapped
        route.dependant.call = wrapped
        app.state.chat_tracing_installed = True
        return

    raise RuntimeError("Could not find the POST /chat route to install tracing.")
