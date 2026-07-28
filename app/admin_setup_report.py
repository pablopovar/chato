from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.api import require_admin
from app.db import fetch_one


router = APIRouter()


@router.get(
    "/admin/intakes/{intake_id}/setup-report",
    dependencies=[Depends(require_admin)],
    response_class=PlainTextResponse,
)
def setup_report(intake_id: str) -> PlainTextResponse:
    intake = fetch_one(
        "SELECT domain, report_path FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(404, "Intake not found.")
    report_path = str(intake.get("report_path") or "").strip()
    if not report_path:
        raise HTTPException(404, "The intake has no completed setup report.")
    path = Path(report_path)
    if not path.is_file():
        raise HTTPException(404, "The website setup report file is missing.")

    filename = f"{intake['domain']}-setup-report.md"
    return PlainTextResponse(
        path.read_text(encoding="utf-8", errors="replace"),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )
