from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from .config import Settings
from .domain_operations import (
    _core_request,
    _normalize_domain,
    _operator_dependency,
    _submission_rows,
)
from .storage import Storage


def install_domain_approval(app: FastAPI, settings: Settings, storage: Storage) -> None:
    if getattr(app.state, "domain_approval_installed", False):
        return
    operator_auth = _operator_dependency(settings)

    def approve_domain(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        pending = next(
            (
                row
                for row in _submission_rows(storage)
                if str(row.get("domain", "")).casefold().rstrip(".") == normalized
                and row.get("status") == "pending_approval"
            ),
            None,
        )
        if pending is None:
            raise HTTPException(404, f"No pending approval exists for {normalized}.")

        created = _core_request(
            settings,
            "POST",
            "/intakes",
            json={
                "website_url": pending["website_url"],
                "email": pending["email"],
                "business_name": pending.get("business_name"),
            },
        )
        site, _site_token = storage.create_site(
            website_url=pending["website_url"],
            email=pending["email"],
            business_name=pending.get("business_name"),
            domain=normalized,
            intake_id=str(created["intake_id"]),
            core_status_token=str(created["status_token"]),
            status=str(created.get("status") or "queued"),
        )
        with storage.connect() as conn:
            conn.execute(
                "UPDATE site_submissions SET status='started', site_id=?, updated_at=? WHERE id=?",
                (
                    site["id"],
                    datetime.now(timezone.utc).isoformat(),
                    pending["id"],
                ),
            )
        return {
            "domain": normalized,
            "email": pending["email"],
            "site_id": site["id"],
            "intake_id": created["intake_id"],
            "status": created.get("status", "queued"),
        }

    app.add_api_route(
        "/v1/admin/domains/{domain}/approve",
        approve_domain,
        methods=["POST"],
        dependencies=[Depends(operator_auth)],
        status_code=202,
    )
    app.state.domain_approval_installed = True
