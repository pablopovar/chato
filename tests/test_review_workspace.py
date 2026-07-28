from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app import db
from app.config import settings as core_settings
from app.services import review_activation, review_workspace


def _prepare_intake(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    users_dir = tmp_path / "users"
    test_settings = replace(
        core_settings,
        data_dir=data_dir,
        users_dir=users_dir,
        public_base_url="https://example.test",
    )
    monkeypatch.setattr(db, "DB_PATH", data_dir / "nerdo.sqlite3")
    monkeypatch.setattr(db, "settings", test_settings)
    monkeypatch.setattr(review_workspace, "settings", test_settings)
    monkeypatch.setattr(review_activation, "settings", test_settings)
    monkeypatch.setattr(review_activation, "send_email", lambda **_kwargs: None)
    db.init_db()

    dataset = data_dir / "intakes" / "intake-1" / "datasets" / "dataset-1"
    pages = dataset / "cleaned" / "pages"
    pages.mkdir(parents=True)
    (pages / "about.md").write_text(
        "---\nsource_url: https://example.com/about\n---\n# About\n\nCanonical page.\n",
        encoding="utf-8",
    )
    summary = data_dir / "intakes" / "intake-1" / "chato-summary.md"
    summary.write_text("# Example\n\n## Business Overview\n\nReviewed summary.\n", encoding="utf-8")
    report = data_dir / "intakes" / "intake-1" / "setup-report.md"
    report.write_text(
        "# Website Setup Report: example.com\n\n"
        "## Nerdo — Data Processing Report\n\nProcessed one page.\n\n"
        "## Chato — Corpus Summary\n\n### Example\n\nReviewed summary.\n\n"
        "## Review Status\n\nReady.\n",
        encoding="utf-8",
    )

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO intakes (
                id, status_token_hash, email, website_url, domain,
                business_name, status, draft_path, report_path,
                dataset_version_id, dataset_path,
                fetched_page_count, document_count, duplicate_count,
                chunk_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "intake-1",
                "hash",
                "owner@example.com",
                "https://example.com",
                "example.com",
                "Example",
                "awaiting_review",
                str(summary),
                str(report),
                "dataset-1",
                str(dataset),
                1,
                1,
                0,
                3,
                "2026-07-28T00:00:00+00:00",
                "2026-07-28T00:05:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO dataset_versions (
                id, intake_id, status, dataset_path,
                fetched_page_count, document_count, duplicate_count,
                chunk_count, created_at, updated_at
            ) VALUES (?, ?, 'ready', ?, 1, 1, 0, 3, ?, ?)
            """,
            (
                "dataset-1",
                "intake-1",
                str(dataset),
                "2026-07-28T00:00:00+00:00",
                "2026-07-28T00:05:00+00:00",
            ),
        )
    return data_dir, users_dir


def test_review_workspace_uses_active_domain_shape_and_promotes_exact_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _data_dir, users_dir = _prepare_intake(tmp_path, monkeypatch)

    workspace = review_workspace.ensure_review_workspace("intake-1")
    root = Path(workspace["workspace"])

    assert (root / "nerdo.json").is_file()
    assert (root / "knowledge.md").read_text(encoding="utf-8").startswith("# Example")
    assert (root / "source-pages" / "about.md").is_file()

    configuration = json.loads((root / "nerdo.json").read_text(encoding="utf-8"))
    configuration.update(
        {
            "model": "reviewed-model",
            "system_prompt": "Reviewed system prompt.",
            "temperature": 0.35,
            "max_tokens": 777,
            "max_results": 5,
            "max_context_chars": 22000,
            "debug": True,
        }
    )
    (root / "nerdo.json").write_text(
        json.dumps(configuration, indent=2) + "\n",
        encoding="utf-8",
    )

    result = review_activation.activate_reviewed_intake(
        "intake-1",
        bot_name=None,
        system_prompt=None,
        allowed_origins=[],
        welcome_subject="Ready",
        welcome_message="Ready",
        test_url=None,
    )

    active = users_dir / "owner" / "example.com"
    promoted = json.loads((active / "nerdo.json").read_text(encoding="utf-8"))
    assert result["domain"] == "example.com"
    assert promoted["model"] == "reviewed-model"
    assert promoted["system_prompt"] == "Reviewed system prompt."
    assert promoted["temperature"] == 0.35
    assert promoted["max_tokens"] == 777
    assert promoted["max_results"] == 5
    assert promoted["max_context_chars"] == 22000
    assert promoted["debug"] is True
    assert "review_only" not in promoted
    assert "review_intake_id" not in promoted
    assert (active / "knowledge.md").is_file()
    assert (active / "source-pages" / "about.md").is_file()
    assert not root.exists()

    with db.connection() as conn:
        status = conn.execute(
            "SELECT status FROM intakes WHERE id = 'intake-1'"
        ).fetchone()[0]
    assert status == "active"
