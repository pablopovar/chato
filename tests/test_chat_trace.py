from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app.chat_trace_install as trace_install
from app.chat_trace_install import install_chat_tracing
from app.services.chat_trace import (
    TraceRecorder,
    current_trace,
    load_session_traces,
    reset_current_trace,
    session_trace_bundle,
    set_current_trace,
    trace_count,
)
from app.services.registry import BotConfig
from app.services.retrieval import search
from nerdo_api import dashboard_domain
from nerdo_api.chat_trace_ui import (
    enhance_dashboard_page,
    install_debug_configuration,
)


def _config(directory: Path) -> BotConfig:
    return BotConfig(
        domain="example.com",
        directory=directory,
        enabled=True,
        debug=True,
        key="abcdefgh",
        name="Example",
        system_prompt="Answer from the supplied material.",
        model="test-model",
        model_base_url="http://model.invalid/v1",
        model_api_key="secret",
        allowed_origins=(),
        max_results=2,
        max_context_chars=18_000,
        temperature=0.1,
        max_tokens=900,
        welcome_message="Ask a question.",
        suggested_questions=(),
    )


def test_trace_is_atomic_persistent_and_bundleable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NERDO_CHAT_TRACE_DIR", str(tmp_path / "traces"))
    recorder = TraceRecorder(
        domain="example.com",
        session_id="session/with unsafe characters",
        request_id="request-1",
    )
    recorder.event("request.received", question="What is this?")
    recorder.event("answer.completed", mode="grounded-model", answer="An answer.")

    path = recorder.close_and_write()

    assert path is not None and path.is_file()
    assert not list(path.parent.glob("*.tmp"))
    assert trace_count("example.com", "session/with unsafe characters") == 1
    traces = load_session_traces("example.com", "session/with unsafe characters")
    assert traces[0]["request_id"] == "request-1"
    assert [item["stage"] for item in traces[0]["events"]] == [
        "request.received",
        "answer.completed",
        "trace.completed",
    ]
    bundle = session_trace_bundle("example.com", "session/with unsafe characters")
    assert bundle["trace_count"] == 1
    assert bundle["traces"][0]["schema"] == "chato.chat-trace.v1"


def test_retrieval_records_all_candidates_and_selected_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NERDO_CHAT_TRACE_DIR", str(tmp_path / "traces"))
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "about.md").write_text(
        "# Example Museum\n\nThe Example Museum preserves the history of transit.",
        encoding="utf-8",
    )
    (corpus / "event.md").write_text(
        "# Museum Event\n\nA museum event about transit history.",
        encoding="utf-8",
    )
    recorder = TraceRecorder("example.com", "session-1", "request-2")
    token = set_current_trace(recorder)
    try:
        hits = search(_config(corpus), "What is the Example Museum?")
    finally:
        reset_current_trace(token)

    event = next(
        item for item in recorder.events if item["stage"] == "retrieval.completed"
    )
    assert event["data"]["candidate_count"] >= 2
    assert event["data"]["selected_count"] == len(hits)
    assert all("text" in item for item in event["data"]["candidates"])


def test_chat_route_installer_records_only_authenticated_debug_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NERDO_CHAT_TRACE_DIR", str(tmp_path / "traces"))
    monkeypatch.setattr(trace_install, "load_bot", lambda _domain: _config(tmp_path))

    class ChatBody(BaseModel):
        domain: str
        key: str
        question: str
        session_id: str | None = None

    app = FastAPI()

    @app.post("/chat")
    def chat(body: ChatBody, request: Request, response: Response) -> dict[str, str]:
        recorder = current_trace()
        if recorder:
            recorder.event("route.inside")
        return {
            "request_id": "route-request",
            "session_id": str(body.session_id),
            "answer": "ok",
        }

    install_chat_tracing(app)
    client = TestClient(app)

    valid = client.post(
        "/chat",
        json={
            "domain": "example.com",
            "key": "abcdefgh",
            "question": "What is this?",
        },
    )
    assert valid.status_code == 200
    session_id = valid.json()["session_id"]
    assert session_id and session_id != "None"
    traces = load_session_traces("example.com", session_id)
    assert traces[0]["request_id"] == "route-request"
    assert any(item["stage"] == "route.inside" for item in traces[0]["events"])

    invalid = client.post(
        "/chat",
        json={
            "domain": "example.com",
            "key": "wrong-key",
            "question": "Do not trace this.",
            "session_id": "unauthorized-session",
        },
    )
    assert invalid.status_code == 200
    assert trace_count("example.com", "unauthorized-session") == 0


def test_dashboard_adds_debug_setting_and_trace_download() -> None:
    page = enhance_dashboard_page(dashboard_domain.DOMAIN_PAGE)

    assert 'id="debug"' in page
    assert "Record full chat traces" in page
    assert "Download Trace" in page
    assert "trace_count" in page


def test_dashboard_debug_setting_is_boolean() -> None:
    install_debug_configuration()
    base = {
        "model": "test-model",
        "system_prompt": "Use the corpus.",
        "temperature": 0.1,
        "max_tokens": 900,
        "max_results": 6,
        "max_context_chars": 18_000,
        "debug": True,
    }

    assert dashboard_domain._validated_update(base)["debug"] is True
    with pytest.raises(Exception, match="debug must be true or false"):
        dashboard_domain._validated_update({**base, "debug": "true"})
