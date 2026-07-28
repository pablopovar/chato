from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.api import require_admin
from app.db import execute, utc_now
from app.schemas import DraftUpdate
from app.services.review_workspace import (
    ensure_review_workspace,
    sync_review_summary,
)
from app.services.setup_report import update_setup_report_summary


router = APIRouter()


def _workspace(intake_id: str) -> dict[str, Any]:
    try:
        return ensure_review_workspace(intake_id)
    except RuntimeError as exc:
        message = str(exc)
        status = 404 if message in {"Intake not found."} else 409
        raise HTTPException(status, message) from exc


@router.get(
    "/admin/intakes/{intake_id}/setup-report",
    dependencies=[Depends(require_admin)],
    response_class=PlainTextResponse,
)
def setup_report(intake_id: str) -> PlainTextResponse:
    workspace = _workspace(intake_id)
    intake = workspace["intake"]
    report_path = Path(workspace["report_path"])
    filename = f"{intake['domain']}-setup-report.md"
    return PlainTextResponse(
        report_path.read_text(encoding="utf-8", errors="replace"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/admin/intakes/{intake_id}/review",
    dependencies=[Depends(require_admin)],
)
def review(intake_id: str) -> dict[str, Any]:
    workspace = _workspace(intake_id)
    intake = workspace["intake"]
    summary_path = Path(workspace["summary_path"])
    report_path = Path(workspace["report_path"])
    crawl = workspace.get("crawl") or {}
    dataset = workspace.get("dataset") or {}
    return {
        "intake": intake,
        "summary": summary_path.read_text(
            encoding="utf-8",
            errors="replace",
        ),
        "report": report_path.read_text(
            encoding="utf-8",
            errors="replace",
        ),
        "workspace": {
            "ready": True,
            "domain_directory": str(workspace["workspace"]),
            "document_count": int(dataset.get("document_count") or intake.get("document_count") or 0),
            "duplicate_count": int(dataset.get("duplicate_count") or intake.get("duplicate_count") or 0),
            "chunk_count": int(dataset.get("chunk_count") or intake.get("chunk_count") or 0),
            "crawl_attempts": int(crawl.get("attempts") or 0),
            "crawl_stop_reason": str(crawl.get("stop_reason") or ""),
        },
    }


@router.put(
    "/admin/intakes/{intake_id}/review-summary",
    dependencies=[Depends(require_admin)],
)
def save_review_summary(
    intake_id: str,
    body: DraftUpdate,
) -> dict[str, str]:
    workspace = _workspace(intake_id)
    intake = workspace["intake"]
    if intake.get("status") != "awaiting_review":
        raise HTTPException(
            409,
            f"The summary cannot be edited while status is {intake.get('status')}.",
        )

    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Chato's corpus summary cannot be empty.")

    summary_path = Path(workspace["summary_path"])
    report_path = Path(workspace["report_path"])
    knowledge_path = Path(workspace["workspace"]) / "knowledge.md"
    original_summary = summary_path.read_text(encoding="utf-8", errors="replace")
    original_report = report_path.read_text(encoding="utf-8", errors="replace")
    original_knowledge = knowledge_path.read_text(encoding="utf-8", errors="replace")

    try:
        temporary = summary_path.with_name(f".{summary_path.name}.tmp")
        temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
        temporary.replace(summary_path)
        update_setup_report_summary(report_path, content)
        sync_review_summary(intake_id, content)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        for path, original in (
            (summary_path, original_summary),
            (report_path, original_report),
            (knowledge_path, original_knowledge),
        ):
            rollback = path.with_name(f".{path.name}.rollback")
            rollback.write_text(original, encoding="utf-8")
            rollback.replace(path)
        raise HTTPException(500, str(exc)) from exc

    saved_at = utc_now()
    execute(
        "UPDATE intakes SET updated_at = ? WHERE id = ?",
        (saved_at, intake_id),
    )
    return {"status": "saved", "saved_at": saved_at}
