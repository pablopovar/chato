from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from .config import Settings
from .domain_operations import (
    _core_request,
    _latest_intake,
    _normalize_domain,
    _operator_dependency,
    _update_sites_by_intake,
)
from .storage import Storage


def _archive_review_workspaces(settings: Settings, domain: str) -> list[str]:
    normalized = _normalize_domain(domain)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archived: list[str] = []
    for config_path in sorted(
        settings.users_dir.glob(f".review-*/{normalized}/nerdo.json")
    ):
        root = config_path.parent
        destination = (
            settings.users_dir
            / ".review-changes"
            / stamp
            / normalized
            / root.parent.name
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(destination))
        archived.append(str(destination))
        try:
            root.parent.rmdir()
        except OSError:
            pass
    return archived


def install_review_changes(
    app: FastAPI,
    settings: Settings,
    storage: Storage,
) -> None:
    if getattr(app.state, "review_changes_installed", False):
        return
    protected = [Depends(_operator_dependency(settings))]

    def request_changes(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        intake = _latest_intake(settings, normalized)
        if intake.get("status") != "awaiting_review":
            raise HTTPException(
                409,
                f"{normalized} cannot be sent back while status is {intake.get('status')}.",
            )
        archived = _archive_review_workspaces(settings, normalized)
        result = _core_request(
            settings,
            "POST",
            f"/admin/intakes/{intake['id']}/retry",
        )
        _update_sites_by_intake(
            storage,
            str(intake["id"]),
            status="queued",
        )
        return {
            "domain": normalized,
            "status": "queued",
            "intake_id": str(intake["id"]),
            "archived_review_workspaces": archived,
            "core": result,
        }

    app.add_api_route(
        "/v1/admin/domains/{domain}/changes",
        request_changes,
        methods=["POST"],
        dependencies=protected,
        status_code=202,
    )
    app.state.review_changes_installed = True
