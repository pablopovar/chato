from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from nerdo_api import dashboard_domain
from nerdo_api.document_foundry import (
    _list_documents,
    _read_document,
    _relative_document_path,
    _save_document,
)
from nerdo_api.document_foundry_ui import enhance_dashboard_page


def create_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    users = tmp_path / "users"
    root = users / "owner" / "example.com"
    (root / "source-pages").mkdir(parents=True)
    (root / "manual-documents").mkdir()
    (root / "nerdo.json").write_text(
        json.dumps({"domain": "example.com", "enabled": True}),
        encoding="utf-8",
    )
    (root / "knowledge.md").write_text("# Knowledge\n", encoding="utf-8")
    (root / "source-pages" / "home.md").write_text(
        "---\nsource_url: https://example.com/\n---\n\n# Home\n",
        encoding="utf-8",
    )
    (root / "manual-documents" / "policy.md").write_text(
        "# Policy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NERDO_USERS_DIR", str(users))
    return root


def test_lists_domain_markdown_by_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = create_domain(tmp_path, monkeypatch)
    hidden = root / ".document-backups" / "source-pages"
    hidden.mkdir(parents=True)
    (hidden / "home.md").write_text("old", encoding="utf-8")

    records = _list_documents("example.com")
    by_path = {record["path"]: record for record in records}

    assert set(by_path) == {
        "knowledge.md",
        "manual-documents/policy.md",
        "source-pages/home.md",
    }
    assert by_path["source-pages/home.md"]["category"] == "Website data"
    assert by_path["source-pages/home.md"]["source_url"] == "https://example.com/"
    assert by_path["manual-documents/policy.md"]["category"] == "Manual document"


def test_edit_is_atomic_backed_up_and_conflict_checked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = create_domain(tmp_path, monkeypatch)
    opened = _read_document("example.com", "source-pages/home.md")

    result = _save_document(
        "example.com",
        "source-pages/home.md",
        opened["content"] + "\nChanged",
        opened["sha256"],
    )

    assert result["available_immediately"] is True
    assert (root / result["backup"]).read_text(encoding="utf-8") == opened["content"]
    assert (root / "source-pages" / "home.md").read_text(encoding="utf-8").endswith(
        "Changed\n"
    )

    with pytest.raises(HTTPException) as conflict:
        _save_document(
            "example.com",
            "source-pages/home.md",
            "stale edit",
            opened["sha256"],
        )
    assert conflict.value.status_code == 409


def test_document_paths_cannot_escape_domain() -> None:
    for value in ("../nerdo.json", "/etc/passwd.md", ".hidden/file.md"):
        with pytest.raises(HTTPException):
            _relative_document_path(value)


def test_domain_dashboard_accepts_foundry_panel() -> None:
    rendered = enhance_dashboard_page(dashboard_domain.DOMAIN_PAGE)
    assert "foundryPanel" in rendered
    assert "Nerdo's Document Foundry" in rendered
    assert "foundry/document" in rendered
