from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from .config import Settings
from .setup_review import _core_json, _operator_json


CHANGES_JS = r'''
$('#reviewChanges').addEventListener('click',async()=>{
  const button=$('#reviewChanges');button.disabled=true;
  $('#reviewNotice').textContent='Sending this domain back for a fresh processing pass…';
  try{
    const response=await fetch(`/dashboard/api/reviews/${encodeURIComponent(reviewIntakeId)}/changes`,{method:'POST',credentials:'same-origin'});
    const payload=await response.json();
    if(!response.ok)throw Error(payload.detail||`HTTP ${response.status}`);
    location.href='/dashboard/';
  }catch(error){
    $('#reviewNotice').textContent=error.message;
    button.disabled=false;
  }
});
'''


def enhance_dashboard_page(page: str) -> str:
    if 'id="reviewChanges"' in page:
        return page
    button = '<button id="reviewActivate" class="primary" type="button" disabled>Activate reviewed domain</button>'
    state_line = "$('#reviewSummary').disabled=!workspaceReady;$('#reviewSaveSummary').disabled=!workspaceReady;$('#reviewActivate').disabled=!activationReady;"
    if (
        button not in page
        or state_line not in page
        or "</script></body></html>" not in page
    ):
        raise RuntimeError("Could not install the send-back review decision.")
    page = page.replace(
        button,
        '<button id="reviewChanges" class="secondary" type="button" disabled>Send back for changes</button>' + button,
        1,
    )
    page = page.replace(
        state_line,
        "$('#reviewSummary').disabled=!workspaceReady;$('#reviewSaveSummary').disabled=!workspaceReady;$('#reviewChanges').disabled=!workspaceReady;$('#reviewActivate').disabled=!activationReady;",
        1,
    )
    page = page.replace(
        "</script></body></html>",
        CHANGES_JS + "</script></body></html>",
        1,
    )
    return page


def install_review_changes_dashboard(app: FastAPI, settings: Settings) -> None:
    if getattr(app.state, "review_changes_dashboard_installed", False):
        return

    def send_back(intake_id: str) -> dict[str, Any]:
        prepared = _core_json(
            settings,
            "POST",
            f"/admin/intakes/{intake_id}/review-workspace",
        )
        intake = prepared.get("intake") or {}
        domain = str(intake.get("domain") or "").strip()
        if not domain:
            raise HTTPException(409, "The intake has no domain.")
        if intake.get("status") != "awaiting_review":
            raise HTTPException(
                409,
                f"The domain cannot be sent back while status is {intake.get('status')}.",
            )
        return _operator_json(
            settings,
            "POST",
            f"/v1/admin/domains/{domain}/changes",
        )

    app.add_api_route(
        "/dashboard/api/reviews/{intake_id}/changes",
        send_back,
        methods=["POST"],
        include_in_schema=False,
    )
    app.state.review_changes_dashboard_installed = True
