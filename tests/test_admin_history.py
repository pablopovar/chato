from __future__ import annotations

from app import admin_history


def test_domain_conversations_is_scoped_to_normalized_domain(monkeypatch) -> None:
    calls: list[tuple[str, tuple]] = []

    def fake_fetch_one(sql: str, params: tuple):
        calls.append((sql, params))
        return {"count": 1}

    def fake_fetch_all(sql: str, params: tuple):
        calls.append((sql, params))
        return [
            {
                "session_id": "session-1",
                "domain": "example.com",
                "created_at": "2026-07-27T10:00:00+00:00",
                "updated_at": "2026-07-27T10:05:00+00:00",
                "message_count": 2,
                "last_user_message": "What do you sell?",
                "last_assistant_message": "Supported answer.",
            }
        ]

    monkeypatch.setattr(admin_history, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(admin_history, "fetch_all", fake_fetch_all)

    result = admin_history.domain_conversations("EXAMPLE.COM.", limit=25, offset=0)

    assert result["domain"] == "example.com"
    assert result["total"] == 1
    assert result["conversations"][0]["session_id"] == "session-1"
    assert calls[0][1] == ("example.com",)
    assert calls[1][1] == ("example.com", 25, 0)


def test_domain_conversation_rejects_cross_domain_session(monkeypatch) -> None:
    monkeypatch.setattr(admin_history, "fetch_one", lambda *_args, **_kwargs: None)

    try:
        admin_history.domain_conversation("example.com", "session-from-another-domain")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("Expected a 404 for a session outside the selected domain.")
