from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api import require_admin
from app.config import settings
from app.db import connection
from app.services.registry import normalize_domain


router = APIRouter()


class DomainResetRequest(BaseModel):
    confirm: bool = False


def _archive_root(domain: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return settings.data_dir / "domain-reset-archive" / stamp / domain


def _trace_root() -> Path:
    configured = os.getenv("NERDO_CHAT_TRACE_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else (settings.data_dir / "chat-traces").resolve()


def _move_if_present(source: Path, destination: Path) -> str | None:
    if not source.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return str(destination)


def reset_domain_state(domain: str) -> dict[str, Any]:
    normalized = normalize_domain(domain)
    with connection() as conn:
        running = conn.execute(
            """
            SELECT j.id
            FROM jobs j
            JOIN intakes i ON i.id = j.intake_id
            WHERE i.domain = ? AND j.status = 'running'
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        if running:
            raise HTTPException(
                409,
                "The domain has a running intake job. Wait for it to finish before resetting.",
            )

        intakes = conn.execute(
            "SELECT id FROM intakes WHERE domain = ? ORDER BY created_at",
            (normalized,),
        ).fetchall()
        intake_ids = [str(row["id"]) for row in intakes]

        conversation_count = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM conversations WHERE domain = ?",
                (normalized,),
            ).fetchone()["count"]
        )

        for intake_id in intake_ids:
            try:
                conn.execute("DELETE FROM chunks_fts WHERE intake_id = ?", (intake_id,))
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM chunks WHERE intake_id = ?", (intake_id,))
            conn.execute("DELETE FROM documents WHERE intake_id = ?", (intake_id,))
            conn.execute("DELETE FROM crawl_pages WHERE intake_id = ?", (intake_id,))
            conn.execute("DELETE FROM crawl_runs WHERE intake_id = ?", (intake_id,))
            conn.execute("DELETE FROM dataset_versions WHERE intake_id = ?", (intake_id,))
            conn.execute("DELETE FROM jobs WHERE intake_id = ?", (intake_id,))
            conn.execute("DELETE FROM intakes WHERE id = ?", (intake_id,))

        conn.execute(
            "DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE domain = ?)",
            (normalized,),
        )
        conn.execute("DELETE FROM conversations WHERE domain = ?", (normalized,))

    archive_root = _archive_root(normalized)
    archived: list[str] = []
    for intake_id in intake_ids:
        moved = _move_if_present(
            settings.data_dir / "intakes" / intake_id,
            archive_root / "intakes" / intake_id,
        )
        if moved:
            archived.append(moved)

    moved_trace = _move_if_present(
        _trace_root() / normalized,
        archive_root / "chat-traces",
    )
    if moved_trace:
        archived.append(moved_trace)

    return {
        "domain": normalized,
        "removed_intakes": len(intake_ids),
        "removed_conversations": conversation_count,
        "archived_paths": archived,
    }


@router.post(
    "/admin/domains/{domain}/reset",
    dependencies=[Depends(require_admin)],
)
def reset_domain(domain: str, body: DomainResetRequest) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "confirm must be true to reset a domain.")
    try:
        return {"status": "reset", **reset_domain_state(domain)}
    except ValueError as exc:
        raise HTTPException(400, "Invalid domain.") from exc
