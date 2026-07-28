from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.api import require_admin
from app.db import execute, fetch_one, utc_now
from app.schemas import DraftUpdate
from app.services.setup_report import update_setup_report_summary


router = APIRouter()


def _review_record(intake_id: str) -> tuple[dict[str, Any], Path, Path]:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(404, "Intake not found.")
    intake.pop("status_token_hash", None)

    summary_value = str(intake.get("draft_path") or "").strip()
    report_value = str(intake.get("report_path") or "").strip()
    if not summary_value:
        raise HTTPException(404, "The intake has no Chato corpus summary.")
    if not report_value:
        raise HTTPException(404, "The intake has no completed setup report.")

    summary_path = Path(summary_value)
    report_path = Path(report_value)
    if not summary_path.is_file():
        raise HTTPException(404, "The Chato corpus summary file is missing.")
    if not report_path.is_file():
        raise HTTPException(404, "The website setup report file is missing.")
    return intake, summary_path, report_path


@router.get(
    "/admin/intakes/{intake_id}/setup-report",
    dependencies=[Depends(require_admin)],
    response_class=PlainTextResponse,
)
def setup_report(intake_id: str) -> PlainTextResponse:
    intake, _summary_path, report_path = _review_record(intake_id)
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
    intake, summary_path, report_path = _review_record(intake_id)
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
    }


@router.put(
    "/admin/intakes/{intake_id}/review-summary",
    dependencies=[Depends(require_admin)],
)
def save_review_summary(
    intake_id: str,
    body: DraftUpdate,
) -> dict[str, str]:
    intake, summary_path, report_path = _review_record(intake_id)
    if intake.get("status") != "awaiting_review":
        raise HTTPException(
            409,
            f"The summary cannot be edited while status is {intake.get('status')}.",
        )

    content = body.content.strip()
    if not content:
        raise HTTPException(400, "Chato's corpus summary cannot be empty.")

    temporary = summary_path.with_name(f".{summary_path.name}.tmp")
    temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
    temporary.replace(summary_path)
    try:
        update_setup_report_summary(report_path, content)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc)) from exc

    saved_at = utc_now()
    execute(
        "UPDATE intakes SET updated_at = ? WHERE id = ?",
        (saved_at, intake_id),
    )
    return {"status": "saved", "saved_at": saved_at}
