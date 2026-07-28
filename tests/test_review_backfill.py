from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app import db
from app.config import settings as core_settings
from app.services import review_workspace


def test_legacy_awaiting_review_is_filled_before_dashboard_redirect(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    users_dir = tmp_path / "users"
    settings = replace(core_settings, data_dir=data_dir, users_dir=users_dir)
    monkeypatch.setattr(db, "DB_PATH", data_dir / "nerdo.sqlite3")
    monkeypatch.setattr(db, "settings", settings)
    monkeypatch.setattr(review_workspace, "settings", settings)
    db.init_db()

    dataset = data_dir / "intakes" / "legacy" / "datasets" / "dataset-1"
    pages = dataset / "cleaned" / "pages"
    pages.mkdir(parents=True)
    (pages / "about.md").write_text(
        "---\nsource_url: https://example.com/about\n---\n# About\n\nMuseum evidence.\n",
        encoding="utf-8",
    )
    old_draft = data_dir / "intakes" / "legacy" / "old-inventory.md"
    old_draft.write_text("# Sources\n\n- about.md\n", encoding="utf-8")

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO intakes (
                id, status_token_hash, email, website_url, domain,
                status, draft_path, dataset_version_id, dataset_path,
                fetched_page_count, document_count, duplicate_count,
                chunk_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                "hash",
                "owner@example.com",
                "https://example.com",
                "example.com",
                "awaiting_review",
                str(old_draft),
                "dataset-1",
                str(dataset),
                1,
                1,
                0,
                4,
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
            ) VALUES (?, ?, 'ready', ?, 1, 1, 0, 4, ?, ?)
            """,
            (
                "dataset-1",
                "legacy",
                str(dataset),
                "2026-07-28T00:00:00+00:00",
                "2026-07-28T00:05:00+00:00",
            ),
        )

    def fake_interpret(_domain: str, _pages: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "# Example Museum\n\n## Business Overview\n\n- Organization type: Museum\n",
            encoding="utf-8",
        )
        return output

    monkeypatch.setattr(review_workspace, "interpret", fake_interpret)

    result = review_workspace.ensure_review_workspace("legacy")
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    knowledge = Path(result["workspace"]) / "knowledge.md"

    assert "## Nerdo — Data Processing Report" in report
    assert "Pages retrieved: 1" in report
    assert "Search passages created: 4" in report
    assert "## Chato — Corpus Summary" in report
    assert "Example Museum" in report
    assert knowledge.read_text(encoding="utf-8").startswith("# Example Museum")
