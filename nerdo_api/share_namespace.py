from __future__ import annotations

from typing import Any

from fastapi import Body, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse

from .config import Settings
from .share_sessions import (
    CLAIM_PAGE,
    COOKIE_NAME,
    SESSION_PAGE,
    ShareStore,
    _core_request,
    _domain_config,
    _parse,
    _shared_chat,
)


PUBLIC_APP_PREFIX = "/nerdo"
PUBLIC_SHARE_ROUTE_PREFIX = f"{PUBLIC_APP_PREFIX}/share"
INTERNAL_SHARE_ROUTE_PREFIX = "/share"


def public_app_base_url(settings: Settings) -> str:
    base = settings.public_base_url.rstrip("/")
    if base.endswith(PUBLIC_APP_PREFIX):
        return base
    return base + PUBLIC_APP_PREFIX


def install_share_namespace(app: FastAPI, settings: Settings) -> None:
    store = ShareStore(settings.database_path)

    def claim_page(claim_token: str) -> HTMLResponse:
        if len(claim_token) < 20:
            raise HTTPException(404, "Share link not found.")
        return HTMLResponse(CLAIM_PAGE)

    def claim(
        claim_token: str,
        response: Response,
    ) -> dict[str, Any]:
        state, record, access_token = store.claim(claim_token)
        if state == "invalid" or record is None:
            raise HTTPException(404, "Share link not found.")
        if state == "used" or access_token is None:
            raise HTTPException(410, "This share link has already been used.")

        public_path = f"{PUBLIC_SHARE_ROUTE_PREFIX}/session/{record['id']}"
        expires_at = _parse(record["expires_at"])
        response.set_cookie(
            key=COOKIE_NAME,
            value=access_token,
            max_age=int(record["duration_hours"]) * 3600,
            expires=expires_at,
            path=public_path,
            secure=settings.public_base_url.startswith("https://"),
            httponly=True,
            samesite="lax",
        )
        return {
            "session_url": public_path,
            "domain": record["domain"],
            "expires_at": record["expires_at"],
        }

    def session_page(
        session_id: str,
        chato_share_access: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        if store.verify(session_id, chato_share_access or "") is None:
            raise HTTPException(
                410,
                "This shared session is unavailable or expired.",
            )
        return HTMLResponse(SESSION_PAGE)

    def state(
        session_id: str,
        chato_share_access: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        record = store.verify(session_id, chato_share_access or "")
        if record is None:
            raise HTTPException(
                410,
                "This shared session is unavailable or expired.",
            )

        config = _domain_config(record["domain"])
        messages: list[dict[str, Any]] = []
        if record.get("core_session_id"):
            history = _core_request(
                settings,
                "GET",
                (
                    f"/admin/domains/{record['domain']}/conversations/"
                    f"{record['core_session_id']}"
                ),
            )
            messages = list(history.get("messages") or [])

        return {
            "share_id": record["id"],
            "domain": record["domain"],
            "name": str(config.get("name") or "Chato"),
            "expires_at": record["expires_at"],
            "messages": messages,
        }

    def message(
        session_id: str,
        payload: dict[str, Any] = Body(...),
        chato_share_access: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        access_token = chato_share_access or ""
        record = store.verify(session_id, access_token)
        if record is None:
            raise HTTPException(
                410,
                "This shared session is unavailable or expired.",
            )

        result = _shared_chat(
            settings,
            record,
            str(payload.get("question") or ""),
        )
        core_session_id = str(result.get("session_id") or "").strip()
        if core_session_id and core_session_id != record.get("core_session_id"):
            if not store.bind_core_session(
                session_id,
                access_token,
                core_session_id,
            ):
                raise HTTPException(
                    410,
                    "This shared session is unavailable or expired.",
                )

        return {
            "answer": str(result.get("answer") or ""),
            "session_id": core_session_id,
        }

    app.add_api_route(
        f"{INTERNAL_SHARE_ROUTE_PREFIX}/{{claim_token}}",
        claim_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        f"{INTERNAL_SHARE_ROUTE_PREFIX}/{{claim_token}}/claim",
        claim,
        methods=["POST"],
        include_in_schema=False,
    )
    app.add_api_route(
        f"{INTERNAL_SHARE_ROUTE_PREFIX}/session/{{session_id}}",
        session_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        f"{INTERNAL_SHARE_ROUTE_PREFIX}/session/{{session_id}}/state",
        state,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        f"{INTERNAL_SHARE_ROUTE_PREFIX}/session/{{session_id}}/messages",
        message,
        methods=["POST"],
        include_in_schema=False,
    )
