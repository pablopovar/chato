from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from app.api import require_admin
from app.schemas import ActivateIntake
from app.services.review_activation import activate_reviewed_intake


ACTIVATION_PATH = "/admin/intakes/{intake_id}/activate"


def install_review_activation(app: FastAPI) -> None:
    if getattr(app.state, "review_activation_installed", False):
        return

    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == ACTIVATION_PATH
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]

    def activate(
        intake_id: str,
        body: ActivateIntake,
    ) -> dict[str, Any]:
        try:
            bot = activate_reviewed_intake(
                intake_id,
                bot_name=body.bot_name,
                system_prompt=body.system_prompt,
                allowed_origins=body.allowed_origins,
                welcome_subject=body.welcome_subject,
                welcome_message=body.welcome_message,
                test_url=body.test_url,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "active", "bot": bot}

    app.add_api_route(
        ACTIVATION_PATH,
        activate,
        methods=["POST"],
        dependencies=[Depends(require_admin)],
    )
    app.state.review_activation_installed = True
