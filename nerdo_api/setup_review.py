from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from . import dashboard_domain
from .config import Settings


REVIEW_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Website setup review</title><style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076;--soft:#eef1f7;--bg:#f3f4f7}*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--navy)}header{padding:22px 5vw;background:var(--navy);color:#fff}.head{display:flex;justify-content:space-between;gap:20px;align-items:end}h1{margin:0;font-size:36px}header p{margin:6px 0 0;color:#d8dbea}header a{color:#fff;text-decoration:none;font-weight:800}main{padding:24px 5vw;max-width:1400px;margin:0 auto}.intro,.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:16px}.intro p{max-width:900px;line-height:1.55}.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.pill{background:var(--soft);padding:6px 9px;border-radius:999px;font-size:12px;font-weight:800}.report{white-space:pre-wrap;line-height:1.55;font-family:inherit;background:#fbfbfd;border:1px solid var(--line);border-radius:9px;padding:18px;max-height:700px;overflow:auto}textarea{width:100%;min-height:620px;border:1px solid var(--line);border-radius:9px;padding:16px;font:inherit;line-height:1.5;color:var(--navy);resize:vertical}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}button,.button{border:0;border-radius:8px;padding:11px 15px;font:inherit;font-weight:850;cursor:pointer;text-decoration:none;display:inline-block}.primary{background:var(--red);color:#fff}.secondary{background:var(--soft);color:var(--navy)}button:disabled{background:#c8cbd5;color:#fff;cursor:not-allowed}.notice{min-height:25px;font-weight:750}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.help{color:var(--muted);line-height:1.5}@media(max-width:900px){.grid{grid-template-columns:1fr}.head{display:block}.head a{display:inline-block;margin-top:12px}}
</style></head><body><header><div class="head"><div><h1 id="title">Website setup review</h1><p>Review Nerdo's processing results and Chato's understanding before activation.</p></div><a href="/dashboard/">All domains</a></div></header><main>
<section class="intro"><h2>Review decision</h2><p>Check what Nerdo retrieved and processed. Then review Chato's corpus summary for identity, offerings, audiences, locations, unsupported inferences, missing information, and stale or contradictory claims. Save corrections before activation.</p><div id="meta" class="meta"></div><p id="notice" class="notice"></p><div class="actions"><a id="download" class="button secondary" href="#">Download full setup report</a><button id="activate" class="primary" disabled>Activate domain</button></div></section>
<div class="grid"><section class="panel"><h2>Nerdo — Data Processing Report</h2><p class="help">This section reports retrieval, cleaning, deduplication, standardization, and search preparation. It is not editable.</p><pre id="report" class="report">Loading report…</pre></section>
<section class="panel"><h2>Chato — Corpus Summary</h2><p class="help">This becomes <code>knowledge.md</code> after activation. Correct it here before approving the domain.</p><textarea id="summary" disabled></textarea><div class="actions"><button id="save" class="secondary" disabled>Save Chato summary</button></div></section></div>
</main><script>
const parts=location.pathname.split('/').filter(Boolean);const intakeId=decodeURIComponent(parts[parts.length-1]);const $=s=>document.querySelector(s);const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let domain='';let status='';
function processingOnly(text){const marker='## Chato — Corpus Summary';const index=String(text||'').indexOf(marker);return index>=0?String(text).slice(0,index).trim():String(text||'').trim()}
async function load(){const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(intakeId)}`,{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);const i=p.intake||{};domain=i.domain||'';status=i.status||'unknown';$('#title').textContent=`Review ${domain||'website setup'}`;$('#meta').innerHTML=`<span class="pill">${esc(status)}</span><span class="pill">${esc(i.document_count||0)} documents</span><span class="pill">${esc(i.duplicate_count||0)} duplicates</span><span class="pill">${esc(i.chunk_count||0)} search passages</span><span class="pill">${esc(i.email||'')}</span>`;$('#download').href=`/dashboard/api/reviews/${encodeURIComponent(intakeId)}/download`;$('#report').textContent=processingOnly(p.report)||`Nerdo is still processing this intake. Current status: ${status}.`;$('#summary').value=p.summary||'';const ready=status==='awaiting_review'&&Boolean(p.summary)&&Boolean(p.report);$('#summary').disabled=!ready;$('#save').disabled=!ready;$('#activate').disabled=!ready;$('#notice').textContent=ready?'Review the report and Chato summary. Save corrections, then activate.':`This domain is not ready for review. Current status: ${status}.`}
$('#save').addEventListener('click',async()=>{$('#save').disabled=true;$('#notice').textContent='Saving Chato summary…';try{const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(intakeId)}/summary`,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:$('#summary').value})});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);$('#notice').textContent=`Saved ${p.saved_at||''}.`;await load()}catch(e){$('#notice').textContent=e.message;$('#save').disabled=false}});
$('#activate').addEventListener('click',async()=>{$('#activate').disabled=true;$('#notice').textContent='Activating domain…';try{const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(intakeId)}/activate`,{method:'POST',credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);location.href=`/dashboard/${encodeURIComponent(domain)}`}catch(e){$('#notice').textContent=e.message;$('#activate').disabled=false}});
load().catch(e=>{$('#notice').textContent=e.message;$('#report').textContent='The review could not be loaded.'});
</script></body></html>'''


def enhance_root_page(page: str) -> str:
    old_header = "Select a domain to configure it and review its conversations."
    page = page.replace(
        old_header,
        "Select an active domain or open a completed intake for review.",
        1,
    )
    start = page.find("async function load(){")
    end = page.find("\nload().catch", start)
    if start < 0 or end < 0:
        raise RuntimeError("Dashboard domain-list script marker was not found.")
    replacement = r'''async function load(){const r=await fetch('/dashboard/api/domains',{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);if(!p.domains.length){root.innerHTML='<div class="empty">No domains or intakes found.</div>';return}root.innerHTML=p.domains.map(x=>{const href=x.href||`/dashboard/${encodeURIComponent(x.domain)}`;const status=x.status||(x.enabled?'active':'disabled');const model=x.model?`<span class="pill">${esc(x.model)}</span>`:'';return `<a class="card" href="${esc(href)}"><div class="domain">${esc(x.domain)}</div><div class="name">${esc(x.name||x.domain)}</div><div class="meta"><span class="pill">${esc(status)}</span>${model}<span class="pill">${esc(x.document_count||0)} documents</span></div></a>`}).join('')}'''
    return page[:start] + replacement + page[end:]


def _core_response(
    settings: Settings,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}))
    headers["X-Admin-Token"] = settings.core_admin_token
    try:
        response = httpx.request(
            method,
            settings.core_base_url.rstrip("/") + path,
            headers=headers,
            timeout=max(settings.request_timeout_seconds, 90),
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Nerdo Core is unavailable: {exc}") from exc
    if response.is_error:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    return response


def _core_json(
    settings: Settings,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    response = _core_response(settings, method, path, **kwargs)
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Nerdo Core returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, "Nerdo Core returned an invalid response.")
    return payload


def _operator_json(
    settings: Settings,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    base_url = os.getenv(
        "NERDO_GATEWAY_BASE_URL",
        "http://127.0.0.1:3400",
    ).rstrip("/")
    headers = dict(kwargs.pop("headers", {}))
    headers["X-Nerdo-Key"] = settings.operator_token
    try:
        response = httpx.request(
            method,
            base_url + path,
            headers=headers,
            timeout=max(settings.request_timeout_seconds, 120),
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Nerdo domain operations API is unavailable: {exc}") from exc
    try:
        payload: Any = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    if not isinstance(payload, dict):
        raise HTTPException(502, "Nerdo domain operations API returned an invalid response.")
    return payload


def _dashboard_domains(settings: Settings) -> list[dict[str, Any]]:
    active = {
        str(row["domain"]): {
            **row,
            "status": "active" if row.get("enabled", True) else "disabled",
            "deployed": True,
            "href": f"/dashboard/{row['domain']}",
        }
        for row in dashboard_domain._domain_rows()
    }
    payload = _core_json(settings, "GET", "/admin/intakes")
    latest: dict[str, dict[str, Any]] = {}
    for intake in payload.get("intakes", []):
        domain = str(intake.get("domain") or "").casefold().rstrip(".")
        if domain and domain not in latest:
            latest[domain] = intake

    rows = dict(active)
    for domain, intake in latest.items():
        status = str(intake.get("status") or "unknown")
        if status == "active" and domain in rows:
            rows[domain]["document_count"] = int(
                intake.get("document_count") or rows[domain].get("document_count") or 0
            )
            continue
        rows[domain] = {
            "domain": domain,
            "name": str(intake.get("business_name") or domain),
            "enabled": False,
            "model": "",
            "document_count": int(intake.get("document_count") or 0),
            "status": status,
            "intake_id": str(intake.get("id") or ""),
            "deployed": domain in active,
            "href": f"/dashboard/reviews/{intake['id']}",
        }
    return [rows[key] for key in sorted(rows)]


def install_setup_review(app: FastAPI, settings: Settings) -> None:
    if getattr(app.state, "setup_review_installed", False):
        return

    def domains() -> dict[str, Any]:
        rows = _dashboard_domains(settings)
        return {"count": len(rows), "domains": rows}

    def review_data(intake_id: str) -> dict[str, Any]:
        intake_payload = _core_json(
            settings,
            "GET",
            f"/admin/intakes/{intake_id}",
        )
        intake = intake_payload.get("intake") or {}
        if not intake.get("report_path") or not intake.get("draft_path"):
            return {"intake": intake, "summary": "", "report": ""}
        return _core_json(
            settings,
            "GET",
            f"/admin/intakes/{intake_id}/review",
        )

    def save_summary(
        intake_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        content = str(payload.get("content") or "")
        return _core_json(
            settings,
            "PUT",
            f"/admin/intakes/{intake_id}/review-summary",
            json={"content": content},
        )

    def activate(intake_id: str) -> dict[str, Any]:
        review = review_data(intake_id)
        intake = review.get("intake") or {}
        domain = str(intake.get("domain") or "").strip()
        if not domain:
            raise HTTPException(409, "The intake has no domain.")
        if intake.get("status") != "awaiting_review":
            raise HTTPException(
                409,
                f"The domain cannot be activated while status is {intake.get('status')}.",
            )
        return _operator_json(
            settings,
            "POST",
            f"/v1/admin/domains/{domain}/activate",
        )

    def download(intake_id: str) -> PlainTextResponse:
        response = _core_response(
            settings,
            "GET",
            f"/admin/intakes/{intake_id}/setup-report",
        )
        disposition = response.headers.get(
            "content-disposition",
            'attachment; filename="website-setup-report.md"',
        )
        return PlainTextResponse(
            response.text,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": disposition,
                "Cache-Control": "private, no-store",
            },
        )

    def review_page(_intake_id: str) -> HTMLResponse:
        return HTMLResponse(REVIEW_PAGE)

    app.add_api_route(
        "/dashboard/api/domains",
        domains,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/reviews/{intake_id}",
        review_data,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/reviews/{intake_id}/summary",
        save_summary,
        methods=["PUT"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/reviews/{intake_id}/activate",
        activate,
        methods=["POST"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/reviews/{intake_id}/download",
        download,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/reviews/{_intake_id}",
        review_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.state.setup_review_installed = True
