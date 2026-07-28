from __future__ import annotations

from typing import Any, Callable

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from . import dashboard_domain
from .config import Settings


STYLE = """
.debug-toggle{display:flex;align-items:center;gap:10px;text-transform:none;letter-spacing:0;font-size:14px;color:var(--navy)}
.debug-toggle input{width:auto;margin:0}
.conversation-row{position:relative;border-bottom:1px solid var(--line);background:#fff}
.conversation-row:last-child{border-bottom:0}
.conversation-row .conversation{border-bottom:0;padding-bottom:8px}
.trace-link{display:inline-block;margin:0 13px 12px;font-size:12px;font-weight:800;color:var(--red);text-decoration:none}
.trace-link:hover{text-decoration:underline}
""".strip()


def install_debug_configuration() -> None:
    if getattr(dashboard_domain, "_chat_trace_debug_installed", False):
        return

    original_safe: Callable[[str, dict[str, Any]], dict[str, Any]] = (
        dashboard_domain._safe_configuration
    )
    original_validated: Callable[[dict[str, Any]], dict[str, Any]] = (
        dashboard_domain._validated_update
    )

    def safe_configuration(domain: str, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            **original_safe(domain, raw),
            "debug": bool(raw.get("debug", False)),
        }

    def validated_update(payload: dict[str, Any]) -> dict[str, Any]:
        result = original_validated(payload)
        debug = payload.get("debug", False)
        if not isinstance(debug, bool):
            raise HTTPException(400, "debug must be true or false.")
        result["debug"] = debug
        return result

    dashboard_domain._safe_configuration = safe_configuration
    dashboard_domain._validated_update = validated_update
    dashboard_domain._chat_trace_debug_installed = True


def _replace_once(page: str, old: str, new: str, label: str) -> str:
    if old not in page:
        raise RuntimeError(f"Could not install chat trace UI: missing {label} marker.")
    return page.replace(old, new, 1)


def enhance_dashboard_page(page: str) -> str:
    page = _replace_once(
        page,
        "</style></head>",
        STYLE + "\n</style></head>",
        "style",
    )
    page = _replace_once(
        page,
        '<label>Maximum context characters<input id="maxContextChars" type="number" min="2000" max="100000" step="500"></label></div>',
        '<label>Maximum context characters<input id="maxContextChars" type="number" min="2000" max="100000" step="500"></label><label class="wide debug-toggle"><input id="debug" type="checkbox">Record full chat traces</label></div>',
        "configuration fields",
    )
    page = _replace_once(
        page,
        "$('#maxContextChars').value=c.max_context_chars;notice.textContent=''",
        "$('#maxContextChars').value=c.max_context_chars;$('#debug').checked=Boolean(c.debug);notice.textContent=''",
        "configuration loader",
    )
    page = _replace_once(
        page,
        "max_context_chars:Number($('#maxContextChars').value)};",
        "max_context_chars:Number($('#maxContextChars').value),debug:$('#debug').checked};",
        "configuration saver",
    )
    old_history = "$('#conversationList').innerHTML=rows.map(x=>`<button class=\"conversation\" data-session=\"${esc(x.session_id)}\"><strong>${esc(x.message_count)} messages</strong><time>${esc(new Date(x.updated_at).toLocaleString())}</time><div class=\"preview\">${esc(x.last_user_message||x.last_assistant_message||'')}</div></button>`).join('')}"
    new_history = "$('#conversationList').innerHTML=rows.map(x=>`<div class=\"conversation-row\"><button class=\"conversation\" data-session=\"${esc(x.session_id)}\"><strong>${esc(x.message_count)} messages</strong><time>${esc(new Date(x.updated_at).toLocaleString())}</time><div class=\"preview\">${esc(x.last_user_message||x.last_assistant_message||'')}</div></button>${Number(x.trace_count||0)>0?`<a class=\"trace-link\" data-trace href=\"/dashboard/api/domains/${encodeURIComponent(domain)}/conversations/${encodeURIComponent(x.session_id)}/trace\" download>Download Trace (${esc(x.trace_count)})</a>`:''}</div>`).join('')}"
    page = _replace_once(page, old_history, new_history, "conversation list")
    return page


def install_trace_download(app: FastAPI, settings: Settings) -> None:
    def download_trace(domain: str, session_id: str) -> Response:
        normalized = dashboard_domain._normalize_domain(domain)
        dashboard_domain._config_path(normalized)
        try:
            response = httpx.get(
                settings.core_base_url.rstrip("/")
                + f"/admin/domains/{normalized}/conversations/{session_id}/trace",
                headers={"X-Admin-Token": settings.core_admin_token},
                timeout=settings.request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Nerdo Core is unavailable: {exc}") from exc

        if response.is_error:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise HTTPException(
                response.status_code,
                detail or response.reason_phrase,
            )

        headers = {
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        }
        disposition = response.headers.get("content-disposition")
        if disposition:
            headers["Content-Disposition"] = disposition
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/json"),
            headers=headers,
        )

    app.add_api_route(
        "/dashboard/api/domains/{domain}/conversations/{session_id}/trace",
        download_trace,
        methods=["GET"],
        include_in_schema=False,
    )
