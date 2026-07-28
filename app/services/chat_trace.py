from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import traceback
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings


LOGGER = logging.getLogger("nerdo.chat-trace")
TRACE_SCHEMA = "chato.chat-trace.v1"
TRACE_BUNDLE_SCHEMA = "chato.chat-trace-bundle.v1"
_CURRENT_TRACE: ContextVar[TraceRecorder | None] = ContextVar(
    "chato_current_trace",
    default=None,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return repr(value)


def _trace_root() -> Path:
    configured = os.getenv("NERDO_CHAT_TRACE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (settings.data_dir / "chat-traces").resolve()


def _session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _session_dir(domain: str, session_id: str) -> Path:
    safe_domain = domain.casefold().rstrip(".")
    return _trace_root() / safe_domain / _session_key(session_id)


@dataclass
class TraceRecorder:
    domain: str
    session_id: str
    request_id: str
    started_at: str = field(default_factory=_utc_now)
    events: list[dict[str, Any]] = field(default_factory=list)
    _started_clock: float = field(default_factory=time.perf_counter, repr=False)
    _sequence: int = field(default=0, repr=False)
    _closed: bool = field(default=False, repr=False)

    def event(self, stage: str, **data: Any) -> None:
        if self._closed:
            return
        self._sequence += 1
        self.events.append(
            {
                "sequence": self._sequence,
                "timestamp": _utc_now(),
                "elapsed_ms": round(
                    (time.perf_counter() - self._started_clock) * 1000,
                    3,
                ),
                "stage": stage,
                "data": _json_safe(data),
            }
        )

    def exception(self, stage: str, exc: BaseException, **data: Any) -> None:
        self.event(
            stage,
            exception={
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
            },
            **data,
        )

    def close_and_write(self) -> Path | None:
        if self._closed:
            return None
        self.event("trace.completed")
        self._closed = True
        finished_at = _utc_now()
        payload = {
            "schema": TRACE_SCHEMA,
            "domain": self.domain,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "elapsed_ms": round(
                (time.perf_counter() - self._started_clock) * 1000,
                3,
            ),
            "events": self.events,
        }
        try:
            target_dir = _session_dir(self.domain, self.session_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{self.request_id}.json"
            temporary = target_dir / f".{self.request_id}.tmp"
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
            return target
        except OSError:
            LOGGER.exception(
                "Could not persist chat trace domain=%s session=%s request=%s",
                self.domain,
                self.session_id,
                self.request_id,
            )
            return None


def set_current_trace(recorder: TraceRecorder) -> Token[TraceRecorder | None]:
    return _CURRENT_TRACE.set(recorder)


def reset_current_trace(token: Token[TraceRecorder | None]) -> None:
    _CURRENT_TRACE.reset(token)


def current_trace() -> TraceRecorder | None:
    return _CURRENT_TRACE.get()


def session_trace_paths(domain: str, session_id: str) -> list[Path]:
    directory = _session_dir(domain, session_id)
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.glob("*.json") if path.is_file()),
        key=lambda path: path.name,
    )


def trace_count(domain: str, session_id: str) -> int:
    return len(session_trace_paths(domain, session_id))


def load_session_traces(domain: str, session_id: str) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for path in session_trace_paths(domain, session_id):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            LOGGER.exception("Could not read chat trace %s", path)
            continue
        if isinstance(payload, dict) and payload.get("schema") == TRACE_SCHEMA:
            traces.append(payload)
    return sorted(
        traces,
        key=lambda item: (
            str(item.get("started_at") or ""),
            str(item.get("request_id") or ""),
        ),
    )


def session_trace_bundle(domain: str, session_id: str) -> dict[str, Any]:
    traces = load_session_traces(domain, session_id)
    return {
        "schema": TRACE_BUNDLE_SCHEMA,
        "domain": domain,
        "session_id": session_id,
        "trace_count": len(traces),
        "generated_at": _utc_now(),
        "traces": traces,
    }
