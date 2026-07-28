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


START_PATH = "/v1/admin/domains/{domain}/start"


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

    dependencies = [Depends(operator_auth)]
    app.add_api_route(
        START_PATH,
        approve_domain,
        methods=["POST"],
        dependencies=dependencies,
        status_code=202,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/approve",
        approve_domain,
        methods=["POST"],
        dependencies=dependencies,
        status_code=202,
        include_in_schema=False,
    )
    app.state.domain_approval_installed = True


def remove_duplicate_start_routes(app: FastAPI) -> None:
    seen = False
    retained = []
    for route in app.router.routes:
        methods = getattr(route, "methods", set()) or set()
        if getattr(route, "path", None) == START_PATH and "POST" in methods:
            if seen:
                continue
            seen = True
        retained.append(route)
    app.router.routes = retained
