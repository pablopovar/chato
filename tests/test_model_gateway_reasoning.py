from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import model_gateway


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, response: FakeResponse, captured: list[dict[str, Any]], **_kwargs: Any) -> None:
        self.response = response
        self.captured = captured

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def post(self, _endpoint: str, **kwargs: Any) -> FakeResponse:
        self.captured.append(kwargs["json"])
        return self.response


def test_reasoning_effort_is_sent_and_content_parts_are_joined(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    response = FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "First "},
                            {"type": "text", "text": {"value": "second"}},
                        ]
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )
    monkeypatch.setattr(
        model_gateway.httpx,
        "Client",
        lambda **kwargs: FakeClient(response, captured, **kwargs),
    )

    answer = model_gateway.chat_completion(
        [{"role": "user", "content": "Summarize."}],
        reasoning_effort="none",
    )

    assert answer == "First second"
    assert captured[0]["reasoning_effort"] == "none"


def test_empty_final_content_reports_reasoning_exhaustion(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []
    response = FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning": "Internal reasoning omitted from the final answer.",
                    },
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "completion_tokens": 2200,
                "completion_tokens_details": {"reasoning_tokens": 2200},
            },
        }
    )
    monkeypatch.setattr(
        model_gateway.httpx,
        "Client",
        lambda **kwargs: FakeClient(response, captured, **kwargs),
    )

    with pytest.raises(RuntimeError) as caught:
        model_gateway.chat_completion(
            [{"role": "user", "content": "Summarize."}],
            reasoning_effort="none",
            max_tokens=2200,
        )

    message = str(caught.value)
    assert "empty final response" in message
    assert "finish_reason=length" in message
    assert "completion_tokens=2200" in message
    assert "reasoning_tokens=2200" in message
    assert "reasoning_present=true" in message
