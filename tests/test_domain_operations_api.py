from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nerdo_api.config import Settings
from nerdo_api.domain_approval import (
    install_domain_approval,
    remove_duplicate_start_routes,
)
from nerdo_api.domain_operations import install_domain_operations
from nerdo_api.storage import Storage
from nerdo_mail.command_dispatch import MailboxDomainCommands


def _settings(tmp_path: Path) -> Settings:
    return Settings(
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


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Settings, Storage]:
    settings = _settings(tmp_path)
    storage = Storage(settings.database_path)
    app = FastAPI()
    install_domain_approval(app, settings, storage)
    install_domain_operations(app, settings, storage)
    remove_duplicate_start_routes(app)
    return TestClient(app), settings, storage


def test_mailbox_initializes_without_database_or_users_directory() -> None:
    settings = SimpleNamespace(
        gateway_base_url="http://nerdo-api:3400",
        operator_token="operator-secret",
        admin_emails=frozenset({"admin@example.com"}),
    )

    commands = MailboxDomainCommands(settings)

    assert commands.settings is settings
    assert not hasattr(commands, "storage")


def test_mailbox_enable_calls_domain_operations_api(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        calls.append((method, url, kwargs))
        return httpx.Response(
            200,
            json={"domain": "example.com", "enabled": False},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", request)
    commands = MailboxDomainCommands(
        SimpleNamespace(
            gateway_base_url="http://nerdo-api:3400",
            operator_token="operator-secret",
            admin_emails=frozenset({"admin@example.com"}),
        )
    )

    assert commands.set_enabled("example.com", False) == "Disabled example.com."
    assert calls == [
        (
            "PUT",
            "http://nerdo-api:3400/v1/admin/domains/example.com/enabled",
            {
                "headers": {"X-Nerdo-Key": "operator-secret"},
                "timeout": 120,
                "json": {"enabled": False},
            },
        )
    ]


def test_domain_documents_are_managed_through_api(tmp_path: Path, monkeypatch) -> None:
    client, settings, _storage = _client(tmp_path, monkeypatch)
    root = settings.users_dir / "owner" / "example.com"
    root.mkdir(parents=True)
    (root / "nerdo.json").write_text(
        json.dumps({"domain": "example.com", "enabled": True}),
        encoding="utf-8",
    )
    (root / "knowledge.md").write_text("# Existing\n", encoding="utf-8")

    monkeypatch.setattr(
        "nerdo_api.domain_operations._core_request",
        lambda *_args, **_kwargs: {"intakes": []},
    )
    headers = {"X-Nerdo-Key": "operator-secret"}

    listed = client.get(
        "/v1/admin/domains/example.com/documents",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()["documents"] == [
        {"path": "knowledge.md", "bytes": len("# Existing\n")}
    ]

    uploaded = client.post(
        "/v1/admin/domains/example.com/documents",
        headers=headers,
        json={
            "files": [
                {
                    "filename": "new.md",
                    "content_base64": "IyBOZXcK",
                }
            ]
        },
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["documents"] == ["mail-imports/new.md"]
    assert (root / "mail-imports" / "new.md").read_text(encoding="utf-8") == "# New\n"


def test_reset_archives_and_creates_fresh_intake(tmp_path: Path, monkeypatch) -> None:
    client, settings, storage = _client(tmp_path, monkeypatch)
    root = settings.users_dir / "pablo" / "example.com"
    root.mkdir(parents=True)
    (root / "nerdo.json").write_text(
        json.dumps({"domain": "example.com", "enabled": True}),
        encoding="utf-8",
    )
    storage.create_site(
        website_url="https://example.com",
        email="owner@example.com",
        business_name="Example",
        domain="example.com",
        intake_id="old-intake",
        core_status_token="old-token",
        status="active",
    )

    calls: list[tuple[str, str]] = []

    def core_request(_settings, method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        if method == "GET" and path == "/admin/intakes":
            return {
                "intakes": [
                    {
                        "id": "old-intake",
                        "domain": "example.com",
                        "email": "owner@example.com",
                        "website_url": "https://example.com",
                        "business_name": "Example",
                        "status": "active",
                        "document_count": 3,
                        "updated_at": "2026-07-27T00:00:00+00:00",
                    }
                ]
            }
        if path == "/admin/domains/example.com/reset":
            return {"status": "reset", "removed_intakes": 1}
        if path == "/intakes":
            return {
                "intake_id": "fresh-intake",
                "status_token": "fresh-token",
                "status": "queued",
            }
        raise AssertionError((method, path))

    monkeypatch.setattr("nerdo_api.domain_operations._core_request", core_request)
    response = client.post(
        "/v1/admin/domains/example.com/reset",
        headers={"X-Nerdo-Key": "operator-secret"},
        json={"confirm": True},
    )

    assert response.status_code == 202
    assert response.json()["intake_id"] == "fresh-intake"
    assert response.json()["status"] == "queued"
    assert not root.exists()
    assert list((settings.users_dir / ".reset").rglob("nerdo.json"))
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT intake_id, status FROM sites WHERE domain='example.com'"
        ).fetchall()
    assert [(row["intake_id"], row["status"]) for row in rows] == [
        ("fresh-intake", "queued")
    ]
    assert ("POST", "/admin/domains/example.com/reset") in calls
    assert calls[-1] == ("POST", "/intakes")


def test_operator_token_is_required(tmp_path: Path, monkeypatch) -> None:
    client, _settings, _storage = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "nerdo_api.domain_operations._core_request",
        lambda *_args, **_kwargs: {"intakes": []},
    )

    response = client.get("/v1/admin/domains")

    assert response.status_code == 401
