from __future__ import annotations

from types import SimpleNamespace

from app.services import chatbot
from app.services.retrieval import SearchHit


def test_public_answer_disables_reasoning(monkeypatch) -> None:
    config = SimpleNamespace(
        domain="example.com",
        system_prompt="Answer from the provided sources.",
        max_context_chars=4000,
        model="test-model",
        model_base_url="http://model.example/v1",
        model_api_key="",
        temperature=0.1,
        max_tokens=900,
    )
    hit = SearchHit(
        title="Example",
        path="source-pages/example.md",
        text="EdOr has visible writing steps.",
        score=1.0,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(chatbot, "search_knowledge", lambda *_args: [hit])

    def fake_completion(_messages, **kwargs):
        captured.update(kwargs)
        return "EdOr has visible writing steps. [1]"

    monkeypatch.setattr(chatbot, "chat_completion", fake_completion)

    answer, mode, hits = chatbot.answer(config, "What does EdOr show?")

    assert mode == "grounded-model"
    assert answer == "EdOr has visible writing steps. [1]"
    assert hits == [hit]
    assert captured["reasoning_effort"] == "none"
