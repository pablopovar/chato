from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import settings


DB_PATH = settings.data_dir / "nerdo.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    definition: str,
) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _init_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            '''
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                intake_id UNINDEXED,
                document_id UNINDEXED,
                title,
                heading,
                body,
                source_url UNINDEXED,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            '''
        )
        return True
    except sqlite3.OperationalError:
        # The canonical data remains in documents/chunks even if a local
        # SQLite build lacks FTS5. The filesystem JSONL index is also written.
        return False


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS intakes (
                id TEXT PRIMARY KEY,
                status_token_hash TEXT NOT NULL,
                email TEXT NOT NULL,
                website_url TEXT NOT NULL,
                domain TEXT NOT NULL,
                business_name TEXT,
                status TEXT NOT NULL,
                clarification_count INTEGER NOT NULL DEFAULT 0,
                draft_path TEXT,
                report_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                intake_id TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                run_after TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (intake_id) REFERENCES intakes(id)
            );

            CREATE TABLE IF NOT EXISTS dataset_versions (
                id TEXT PRIMARY KEY,
                intake_id TEXT NOT NULL,
                status TEXT NOT NULL,
                dataset_path TEXT NOT NULL,
                manifest_path TEXT,
                crawl_run_id TEXT,
                fetched_page_count INTEGER NOT NULL DEFAULT 0,
                document_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (intake_id) REFERENCES intakes(id)
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id TEXT PRIMARY KEY,
                intake_id TEXT NOT NULL,
                dataset_version_id TEXT NOT NULL,
                start_url TEXT NOT NULL,
                status TEXT NOT NULL,
                robots_url TEXT,
                robots_status INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                fetched_pages INTEGER NOT NULL DEFAULT 0,
                accepted_pages INTEGER NOT NULL DEFAULT 0,
                skipped_pages INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                stop_reason TEXT,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                FOREIGN KEY (intake_id) REFERENCES intakes(id),
                FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id)
            );

            CREATE TABLE IF NOT EXISTS crawl_pages (
                id TEXT PRIMARY KEY,
                crawl_run_id TEXT NOT NULL,
                intake_id TEXT NOT NULL,
                requested_url TEXT NOT NULL,
                final_url TEXT,
                canonical_hint TEXT,
                parent_url TEXT,
                depth INTEGER NOT NULL,
                status_code INTEGER,
                content_type TEXT,
                bytes_read INTEGER NOT NULL DEFAULT 0,
                content_sha256 TEXT,
                raw_path TEXT,
                title TEXT,
                language TEXT,
                meta_description TEXT,
                noindex INTEGER NOT NULL DEFAULT 0,
                nofollow INTEGER NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL,
                skip_reason TEXT,
                delay_seconds REAL,
                fetched_at TEXT NOT NULL,
                FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id),
                FOREIGN KEY (intake_id) REFERENCES intakes(id)
            );

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                intake_id TEXT NOT NULL,
                dataset_version_id TEXT NOT NULL,
                crawl_page_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                title TEXT NOT NULL,
                language TEXT,
                meta_description TEXT,
                status TEXT NOT NULL,
                duplicate_of TEXT,
                duplicate_reason TEXT,
                raw_path TEXT NOT NULL,
                clean_path TEXT,
                content_sha256 TEXT NOT NULL,
                normalized_sha256 TEXT NOT NULL,
                word_count INTEGER NOT NULL,
                cleaned_text TEXT NOT NULL,
                markdown TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (intake_id) REFERENCES intakes(id),
                FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id),
                FOREIGN KEY (crawl_page_id) REFERENCES crawl_pages(id),
                FOREIGN KEY (duplicate_of) REFERENCES documents(id)
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                intake_id TEXT NOT NULL,
                dataset_version_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                title TEXT NOT NULL,
                heading TEXT,
                source_url TEXT NOT NULL,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (intake_id) REFERENCES intakes(id),
                FOREIGN KEY (dataset_version_id) REFERENCES dataset_versions(id),
                FOREIGN KEY (document_id) REFERENCES documents(id),
                UNIQUE(document_id, ordinal)
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_ready
                ON jobs(status, run_after, id);
            CREATE INDEX IF NOT EXISTS idx_intakes_status
                ON intakes(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_dataset_versions_intake
                ON dataset_versions(intake_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_crawl_pages_run
                ON crawl_pages(crawl_run_id, outcome, depth);
            CREATE INDEX IF NOT EXISTS idx_crawl_pages_final_url
                ON crawl_pages(intake_id, final_url);
            CREATE INDEX IF NOT EXISTS idx_documents_intake
                ON documents(intake_id, dataset_version_id, status);
            CREATE INDEX IF NOT EXISTS idx_documents_hash
                ON documents(intake_id, normalized_sha256);
            CREATE INDEX IF NOT EXISTS idx_documents_duplicate
                ON documents(duplicate_of);
            CREATE INDEX IF NOT EXISTS idx_chunks_document
                ON chunks(document_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_chunks_intake
                ON chunks(intake_id, dataset_version_id);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
                ON messages(conversation_id, id);
            '''
        )

        _ensure_column(conn, "intakes", "dataset_version_id", "TEXT")
        _ensure_column(conn, "intakes", "dataset_path", "TEXT")
        _ensure_column(conn, "intakes", "report_path", "TEXT")
        _ensure_column(
            conn,
            "intakes",
            "fetched_page_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "intakes",
            "document_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "intakes",
            "duplicate_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(
            conn,
            "intakes",
            "chunk_count",
            "INTEGER NOT NULL DEFAULT 0",
        )

        fts_enabled = _init_fts(conn)
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS runtime_capabilities (
                name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                detail TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
        conn.execute(
            '''
            INSERT INTO runtime_capabilities (name, enabled, detail, updated_at)
            VALUES ('sqlite_fts5', ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                enabled = excluded.enabled,
                detail = excluded.detail,
                updated_at = excluded.updated_at
            ''',
            (
                1 if fts_enabled else 0,
                "SQLite FTS5 full-text index",
                utc_now(),
            ),
        )


def execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    with connection() as conn:
        conn.execute(sql, params)


def fetch_one(
    sql: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def fetch_all(
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def enqueue_job(
    kind: str,
    intake_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    now = utc_now()
    with connection() as conn:
        conn.execute(
            '''
            INSERT INTO jobs (
                kind, intake_id, payload_json, status,
                attempts, run_after, created_at, updated_at
            )
            VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
            ''',
            (
                kind,
                intake_id,
                json.dumps(payload or {}, ensure_ascii=False),
                now,
                now,
                now,
            ),
        )


def claim_next_job() -> dict[str, Any] | None:
    now = utc_now()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            '''
            SELECT *
            FROM jobs
            WHERE status = 'queued' AND run_after <= ?
            ORDER BY id
            LIMIT 1
            ''',
            (now,),
        ).fetchone()

        if not row:
            return None

        conn.execute(
            '''
            UPDATE jobs
            SET status = 'running',
                attempts = attempts + 1,
                updated_at = ?
            WHERE id = ?
            ''',
            (now, row["id"]),
        )

        claimed = dict(row)
        claimed["payload"] = json.loads(claimed.pop("payload_json"))
        return claimed


def finish_job(job_id: int) -> None:
    execute(
        "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
        (utc_now(), job_id),
    )


def fail_job(job_id: int, error: str) -> None:
    execute(
        '''
        UPDATE jobs
        SET status = 'failed', error = ?, updated_at = ?
        WHERE id = ?
        ''',
        (error[:4000], utc_now(), job_id),
    )
