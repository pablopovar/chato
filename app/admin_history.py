from __future__ import annotations

import hmac
import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response

from app.config import settings
from app.db import fetch_all, fetch_one
from app.services.chat_trace import session_trace_bundle, trace_count
from app.services.registry import normalize_domain


router = APIRouter()


def require_admin(
    x_admin_token: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin API is not configured.")
    if not x_admin_token or not hmac.compare_digest(
        x_admin_token,
        settings.admin_token,
    ):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


def _domain(value: str) -> str:
    try:
        return normalize_domain(value)
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid domain.") from exc


def _conversation(domain: str, session_id: str) -> dict[str, Any]:
    conversation = fetch_one(
        """
        SELECT id AS session_id, domain, created_at, updated_at
        FROM conversations
        WHERE id = ? AND domain = ?
        """,
        (session_id, domain),
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.get(
    "/admin/domains/{domain}/conversations",
    dependencies=[Depends(require_admin)],
)
def domain_conversations(
    domain: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    normalized = _domain(domain)
    total_row = fetch_one(
        "SELECT COUNT(*) AS count FROM conversations WHERE domain = ?",
        (normalized,),
    )
    rows = fetch_all(
        """
        SELECT
            c.id AS session_id,
            c.domain,
            c.created_at,
            c.updated_at,
            (
                SELECT COUNT(*)
                FROM messages m
                WHERE m.conversation_id = c.id
            ) AS message_count,
            (
                SELECT m.content
                FROM messages m
                WHERE m.conversation_id = c.id AND m.role = 'user'
                ORDER BY m.id DESC
                LIMIT 1
            ) AS last_user_message,
            (
                SELECT m.content
                FROM messages m
                WHERE m.conversation_id = c.id AND m.role = 'assistant'
                ORDER BY m.id DESC
                LIMIT 1
            ) AS last_assistant_message
        FROM conversations c
        WHERE c.domain = ?
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ? OFFSET ?
        """,
        (normalized, limit, offset),
    )
    for row in rows:
        row["trace_count"] = trace_count(normalized, str(row["session_id"]))
    return {
        "domain": normalized,
        "total": int((total_row or {}).get("count") or 0),
        "limit": limit,
        "offset": offset,
        "conversations": rows,
    }


@router.get(
    "/admin/domains/{domain}/conversations/{session_id}",
    dependencies=[Depends(require_admin)],
)
def domain_conversation(
    domain: str,
    session_id: str,
) -> dict[str, Any]:
    normalized = _domain(domain)
    conversation = _conversation(normalized, session_id)
    messages = fetch_all(
        """
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id
        """,
        (session_id,),
    )
    return {
        "conversation": {
            **conversation,
            "trace_count": trace_count(normalized, session_id),
        },
        "messages": messages,
    }


@router.get(
    "/admin/domains/{domain}/conversations/{session_id}/trace",
    dependencies=[Depends(require_admin)],
)
def download_conversation_trace(
    domain: str,
    session_id: str,
) -> Response:
    normalized = _domain(domain)
    _conversation(normalized, session_id)
    bundle = session_trace_bundle(normalized, session_id)
    if not bundle["trace_count"]:
        raise HTTPException(status_code=404, detail="No debug trace exists for this conversation.")

    safe_session = re.sub(r"[^a-zA-Z0-9._-]+", "-", session_id).strip("-.")[:48]
    filename = f"{normalized}-{safe_session or 'session'}-trace.json"
    return Response(
        content=json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
