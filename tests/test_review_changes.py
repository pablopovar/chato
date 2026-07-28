from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nerdo_api import review_changes
from nerdo_api.config import Settings
from nerdo_api.storage import Storage


def test_send_back_queues_core_and_archives_review_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(
        core_base_url="http://core.invalid",
        core_admin_token="core-admin",
        operator_token="operator-secret",
        database_path=tmp_path / "gateway.sqlite3",
        users_dir=tmp_path / "users",
        request_timeout_seconds=2,
        verify_timeout_seconds=2,
        public_base_url="https://example.invalid",
        widget_script_url="",
    )
    storage = Storage(settings.database_path)
    workspace = settings.users_dir / ".review-intake-1" / "example.com"
    workspace.mkdir(parents=True)
    (workspace / "nerdo.json").write_text("{}\n", encoding="utf-8")
    (workspace / "knowledge.md").write_text("# Review\n", encoding="utf-8")

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        review_changes,
        "_latest_intake",
        lambda _settings, _domain: {
            "id": "intake-1",
            "domain": "example.com",
            "status": "awaiting_review",
        },
    )
    monkeypatch.setattr(
        review_changes,
        "_core_request",
        lambda _settings, method, path, **_kwargs: calls.append((method, path)) or {"status": "queued"},
    )
    monkeypatch.setattr(
        review_changes,
        "_update_sites_by_intake",
        lambda *_args, **_kwargs: None,
    )

    app = FastAPI()
    review_changes.install_review_changes(app, settings, storage)
    client = TestClient(app)
    response = client.post(
        "/v1/admin/domains/example.com/changes",
        headers={"X-Nerdo-Key": "operator-secret"},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert calls == [("POST", "/admin/intakes/intake-1/retry")]
    assert not workspace.exists()
    assert list((settings.users_dir / ".review-changes").rglob("knowledge.md"))
