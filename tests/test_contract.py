from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from nerdo_api.config import Settings
from nerdo_api.core_client import CoreClient
from nerdo_api.main import create_app
from nerdo_api.storage import Storage


def core_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path == "/intakes":
        return httpx.Response(201, json={"intake_id": "intake_1", "status_token": "status_1", "status": "queued"})
    if request.method == "GET" and path == "/intakes/intake_1":
        return httpx.Response(200, json={"intake_id": "intake_1", "status": "awaiting_review"})
    if request.method == "POST" and path == "/admin/intakes/intake_1/retry":
        return httpx.Response(202, json={"status": "queued"})
    if request.method == "GET" and path == "/admin/intakes/intake_1/dataset/documents":
        return httpx.Response(200, json={"documents": [
            {"document_id": "d1", "source_url": "https://example.com/", "title": "Home", "content_sha256": "a", "word_count": 10, "cleaned_text": "The support plan costs 10 dollars."},
            {"document_id": "d2", "source_url": "https://example.com/pricing", "title": "Pricing", "content_sha256": "b", "word_count": 10, "cleaned_text": "The support plan costs 20 dollars."},
        ]})
    if request.method == "GET" and path == "/admin/intakes/intake_1/dataset/search":
        return httpx.Response(200, json={"results": [{"text": "The support plan costs 10 dollars.", "source_url": "https://example.com/"}]})
    if request.method == "POST" and path == "/chat":
        return httpx.Response(200, json={"answer": "Grounded answer", "sources": []})
    return httpx.Response(404, json={"detail": f"Unhandled {request.method} {path}"})


def make_client():
    temp = tempfile.TemporaryDirectory()
    settings = Settings(
        core_base_url="http://core.test",
        core_admin_token="admin",
        operator_token="nerdo-secret",
        database_path=Path(temp.name) / "gateway.sqlite3",
        request_timeout_seconds=5,
        verify_timeout_seconds=2,
        public_base_url="https://chato.povarchik.com",
        widget_script_url="https://chato.povarchik.com/widget.js",
    )
    core = CoreClient("http://core.test", "admin", transport=httpx.MockTransport(core_handler))
    app = create_app(settings, Storage(settings.database_path), core)
    return temp, TestClient(app)


def test_full_nerdo_contract():
    temp, client = make_client()
    try:
        created = client.post("/v1/sites", json={"website_url": "https://example.com", "email": "owner@example.com"})
        assert created.status_code == 201, created.text
        site = created.json()
        site_id = site["site_id"]
        site_headers = {"X-Site-Token": site["site_token"]}
        nerdo_headers = {"X-Nerdo-Key": "nerdo-secret"}

        status = client.get(f"/v1/sites/{site_id}", headers=site_headers)
        assert status.status_code == 200
        assert status.json()["status"] == "awaiting_review"

        conversation = client.post(
            "/v1/conversations",
            headers=nerdo_headers,
            json={"persona": "nerdo", "site_id": site_id},
        )
        assert conversation.status_code == 201
        conversation_id = conversation.json()["conversation_id"]

        refresh = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            headers=nerdo_headers,
            json={"content": "Nerdo, update the website sources."},
        )
        assert refresh.status_code == 200, refresh.text
        operation_id = refresh.json()["operation_id"]
        polled = client.get(f"/v1/operations/{operation_id}", headers=nerdo_headers)
        assert polled.status_code == 200
        assert polled.json()["status"] == "completed"

        changes = client.post(
            f"/v1/sites/{site_id}/sources/changes",
            headers=nerdo_headers,
            json={"capture_current": True},
        )
        assert changes.status_code == 200

        contradictions = client.post(
            f"/v1/sites/{site_id}/knowledge/contradictions",
            headers=nerdo_headers,
            json={"minimum_confidence": 0.4, "limit": 20},
        )
        assert contradictions.status_code == 200
        assert contradictions.json()["result"]["finding_count"] >= 1

        diagnosis = client.post(
            f"/v1/sites/{site_id}/answers/diagnose",
            headers=nerdo_headers,
            json={"question": "What does support cost?", "answer": "Support costs 10 dollars."},
        )
        assert diagnosis.status_code == 200
        assert diagnosis.json()["result"]["classification"] in {"supported", "partially_supported", "weakly_supported"}

        integration = client.post(
            f"/v1/sites/{site_id}/integrations",
            headers=nerdo_headers,
            json={
                "kind": "wordpress",
                "target_url": "https://example.com",
                "configuration": {"widget_script_url": "https://chato.povarchik.com/widget.js"},
            },
        )
        assert integration.status_code == 201
        assert "embed_code" in integration.json()["configuration"]
    finally:
        temp.cleanup()


def test_nerdo_requires_operator_key():
    temp, client = make_client()
    try:
        response = client.post("/v1/conversations", json={"persona": "nerdo"})
        assert response.status_code == 401
    finally:
        temp.cleanup()
