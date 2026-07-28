from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app import db
from app.config import settings as core_settings
from app.services import review_workspace


def _settings(tmp_path: Path):
    data_dir = tmp_path / "data"
    users_dir = tmp_path / "users"
    return replace(core_settings, data_dir=data_dir, users_dir=users_dir)


def test_review_workspace_opens_when_chato_summary_generation_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", settings.data_dir / "nerdo.sqlite3")
    monkeypatch.setattr(db, "settings", settings)
    monkeypatch.setattr(review_workspace, "settings", settings)
    db.init_db()

    dataset_dir = settings.data_dir / "intakes" / "legacy" / "datasets" / "dataset-1"
    pages = dataset_dir / "cleaned" / "pages"
    pages.mkdir(parents=True)
    (pages / "about.md").write_text(
        "---\nsource_url: https://example.com/about\n---\n# About\n\nMuseum evidence.\n",
        encoding="utf-8",
    )

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO intakes (
                id, status_token_hash, email, website_url, domain,
                status, dataset_version_id, dataset_path,
                fetched_page_count, document_count, duplicate_count,
                chunk_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                "hash",
                "owner@example.com",
                "https://example.com",
                "example.com",
                "awaiting_review",
                "dataset-1",
                str(dataset_dir),
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
                str(dataset_dir),
                "2026-07-28T00:00:00+00:00",
                "2026-07-28T00:05:00+00:00",
            ),
        )

    monkeypatch.setattr(
        review_workspace,
        "interpret",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    prepared = review_workspace.prepare_review_workspace("legacy")

    assert Path(prepared["workspace"], "nerdo.json").is_file()
    assert Path(prepared["workspace"], "source-pages", "about.md").is_file()
    assert "## Nerdo — Data Processing Report" in Path(prepared["report_path"]).read_text(
        encoding="utf-8"
    )

    reviewed = review_workspace.ensure_review_workspace("legacy")

    assert reviewed["summary_ready"] is False
    assert "model unavailable" in str(reviewed["summary_error"])
    assert Path(reviewed["workspace"], "nerdo.json").is_file()
    assert "Generation error" in Path(reviewed["report_path"]).read_text(encoding="utf-8")


def test_review_pages_are_reconstructed_from_database_when_files_moved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(db, "DB_PATH", settings.data_dir / "nerdo.sqlite3")
    monkeypatch.setattr(db, "settings", settings)
    monkeypatch.setattr(review_workspace, "settings", settings)
    db.init_db()

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO intakes (
                id, status_token_hash, email, website_url, domain,
                status, dataset_version_id, dataset_path,
                fetched_page_count, document_count, duplicate_count,
                chunk_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                "hash",
                "owner@example.com",
                "https://example.com",
                "example.com",
                "awaiting_review",
                "dataset-1",
                str(tmp_path / "missing-dataset"),
                1,
                1,
                0,
                1,
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
            ) VALUES (?, ?, 'ready', ?, 1, 1, 0, 1, ?, ?)
            """,
            (
                "dataset-1",
                "legacy",
                str(tmp_path / "missing-dataset"),
                "2026-07-28T00:00:00+00:00",
                "2026-07-28T00:05:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO crawl_runs (
                id, intake_id, dataset_version_id, start_url, status,
                attempts, fetched_pages, accepted_pages, skipped_pages,
                total_bytes, started_at
            ) VALUES (?, ?, ?, ?, 'complete', 1, 1, 1, 0, 100, ?)
            """,
            (
                "crawl-1",
                "legacy",
                "dataset-1",
                "https://example.com",
                "2026-07-28T00:00:00+00:00",
            ),
        )
        conn.execute(
            "UPDATE dataset_versions SET crawl_run_id='crawl-1' WHERE id='dataset-1'"
        )
        conn.execute(
            """
            INSERT INTO crawl_pages (
                id, crawl_run_id, intake_id, requested_url, final_url,
                depth, status_code, content_type, bytes_read,
                noindex, nofollow, outcome, fetched_at
            ) VALUES (?, ?, ?, ?, ?, 0, 200, 'text/html', 100, 0, 0, 'accepted', ?)
            """,
            (
                "page-1",
                "crawl-1",
                "legacy",
                "https://example.com/about",
                "https://example.com/about",
                "2026-07-28T00:01:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO documents (
                id, intake_id, dataset_version_id, crawl_page_id,
                source_url, canonical_url, title, status,
                raw_path, content_sha256, normalized_sha256,
                word_count, cleaned_text, markdown, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'canonical', ?, ?, ?, 2, ?, ?, ?, ?)
            """,
            (
                "doc-1",
                "legacy",
                "dataset-1",
                "page-1",
                "https://example.com/about",
                "https://example.com/about",
                "About",
                "/missing/raw.html",
                "raw-hash",
                "normalized-hash",
                "Museum evidence.",
                "---\nsource_url: https://example.com/about\n---\n# About\n\nMuseum evidence.\n",
                "2026-07-28T00:02:00+00:00",
                "2026-07-28T00:02:00+00:00",
            ),
        )

    prepared = review_workspace.prepare_review_workspace("legacy")
    materialized = Path(prepared["pages_dir"])

    assert materialized.name == "review-source-pages"
    assert len(list(materialized.glob("*.md"))) == 1
    assert "Museum evidence" in next(materialized.glob("*.md")).read_text(encoding="utf-8")
