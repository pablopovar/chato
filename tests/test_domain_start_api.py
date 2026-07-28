from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nerdo_api.config import Settings
from nerdo_api.domain_approval import (
    START_PATH,
    install_domain_approval,
    remove_duplicate_start_routes,
)
from nerdo_api.domain_operations import install_domain_operations
from nerdo_api.storage import Storage


def test_start_uses_pending_submission_without_direct_channel_storage(
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
    with storage.connect() as conn:
        conn.execute(
            """
            CREATE TABLE site_submissions (
                id TEXT PRIMARY KEY,
                website_url TEXT NOT NULL,
                email TEXT NOT NULL,
                business_name TEXT,
                domain TEXT NOT NULL,
                status TEXT NOT NULL,
                site_id TEXT,
                intro_email_status TEXT NOT NULL DEFAULT 'not_sent',
                intro_email_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO site_submissions (
                id, website_url, email, business_name, domain,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?)
            """,
            (
                "submission-1",
                "https://example.com",
                "owner@example.com",
                "Example",
                "example.com",
                "2026-07-27T00:00:00+00:00",
                "2026-07-27T00:00:00+00:00",
            ),
        )

    def core_request(_settings, method: str, path: str, **_kwargs: Any):
        if method == "POST" and path == "/intakes":
            return {
                "intake_id": "intake-1",
                "status_token": "status-token",
                "status": "queued",
            }
        if method == "GET" and path == "/admin/intakes":
            return {"intakes": []}
        raise AssertionError((method, path))

    monkeypatch.setattr("nerdo_api.domain_approval._core_request", core_request)
    monkeypatch.setattr("nerdo_api.domain_operations._core_request", core_request)

    app = FastAPI()
    install_domain_approval(app, settings, storage)
    install_domain_operations(app, settings, storage)
    remove_duplicate_start_routes(app)
    assert sum(
        1
        for route in app.routes
        if getattr(route, "path", None) == START_PATH
        and "POST" in (getattr(route, "methods", set()) or set())
    ) == 1

    response = TestClient(app).post(
        "/v1/admin/domains/example.com/start",
        headers={"X-Nerdo-Key": "operator-secret"},
    )

    assert response.status_code == 202
    assert response.json()["intake_id"] == "intake-1"
    with storage.connect() as conn:
        submission = conn.execute(
            "SELECT status, site_id FROM site_submissions WHERE id='submission-1'"
        ).fetchone()
        site = conn.execute(
            "SELECT intake_id, status FROM sites WHERE domain='example.com'"
        ).fetchone()
    assert submission["status"] == "started"
    assert submission["site_id"]
    assert site["intake_id"] == "intake-1"
    assert site["status"] == "queued"
