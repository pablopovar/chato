from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from .config import Settings
from .storage import Storage


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
MAX_FILES = 50
MAX_FILE_BYTES = 2_000_000


def _normalize_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(400, "Invalid domain.") from exc
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise HTTPException(400, "Invalid domain.")
    return domain


def _safe_filename(value: str) -> str:
    supplied = Path(value or "document.md").name
    suffix = Path(supplied).suffix.casefold()
    if suffix not in {".md", ".markdown"}:
        raise HTTPException(400, f"{supplied}: only Markdown files are accepted.")
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(supplied).stem).strip("-.")
    return f"{(stem or 'document')[:120]}.md"


def _users_dir() -> Path:
    return Path(os.getenv("NERDO_USERS_DIR", "/app/users")).resolve()


def _domain_dir(domain: str) -> Path | None:
    candidates = sorted(_users_dir().glob(f"*/{domain}/nerdo.json"))
    return candidates[0].parent if candidates else None


def _core_request(settings: Settings, method: str, path: str, **kwargs: Any) -> Any:
    headers = dict(kwargs.pop("headers", {}))
    headers["X-Admin-Token"] = settings.core_admin_token
    try:
        with httpx.Client(
            base_url=settings.core_base_url.rstrip("/"),
            timeout=settings.request_timeout_seconds,
        ) as client:
            response = client.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Nerdo Core is unavailable: {exc}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    return payload


def _submissions(storage: Storage) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='site_submissions'"
        ).fetchone()
        if not exists:
            return []
        rows = conn.execute(
            """
            SELECT domain, email, status, created_at, updated_at
            FROM site_submissions
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _active_domains() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = _users_dir()
    if not root.is_dir():
        return result
    for config_path in sorted(root.glob("*/*/nerdo.json")):
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            domain = _normalize_domain(str(raw.get("domain") or config_path.parent.name))
        except Exception:
            continue
        manual_dir = config_path.parent / "manual-documents"
        result.setdefault(
            domain,
            {
                "domain": domain,
                "email": None,
                "status": "active" if raw.get("enabled", True) else "disabled",
                "updated_at": None,
                "intake_id": None,
                "document_count": len(list(config_path.parent.rglob("*.md"))),
                "manual_document_count": len(list(manual_dir.glob("*.md"))) if manual_dir.is_dir() else 0,
                "can_add_documents": True,
            },
        )
    return result


def _domains(settings: Settings, storage: Storage) -> list[dict[str, Any]]:
    payload = _core_request(settings, "GET", "/admin/intakes")
    active = _active_domains()
    by_domain: dict[str, dict[str, Any]] = {}

    for row in payload.get("intakes", []):
        domain = str(row.get("domain") or "").casefold()
        if not domain or domain in by_domain:
            continue
        active_row = active.get(domain)
        by_domain[domain] = {
            "domain": domain,
            "email": row.get("email"),
            "status": row.get("status"),
            "updated_at": row.get("updated_at"),
            "intake_id": row.get("id"),
            "document_count": int(row.get("document_count") or 0)
            + int((active_row or {}).get("manual_document_count") or 0),
            "manual_document_count": int((active_row or {}).get("manual_document_count") or 0),
            "can_add_documents": active_row is not None,
        }

    for row in _submissions(storage):
        domain = str(row.get("domain") or "").casefold()
        if not domain or domain in by_domain:
            continue
        active_row = active.get(domain)
        by_domain[domain] = {
            "domain": domain,
            "email": row.get("email"),
            "status": row.get("status") or "pending_approval",
            "updated_at": row.get("updated_at"),
            "intake_id": None,
            "document_count": int((active_row or {}).get("document_count") or 0),
            "manual_document_count": int((active_row or {}).get("manual_document_count") or 0),
            "can_add_documents": active_row is not None,
        }

    for domain, row in active.items():
        by_domain.setdefault(domain, row)

    return sorted(
        by_domain.values(),
        key=lambda row: str(row.get("updated_at") or ""),
        reverse=True,
    )


DASHBOARD = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nerdo — Domains</title>
<style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076}*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f4f7;color:var(--navy)}
header{padding:28px 5vw;background:var(--navy);color:#fff;display:flex;justify-content:space-between;align-items:end}h1{margin:0;font-size:40px}header p{margin:6px 0 0;color:#d8dbea}
main{padding:28px 5vw}.panel{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}table{width:100%;border-collapse:collapse}th,td{padding:14px 16px;text-align:left;border-bottom:1px solid var(--line)}th{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);background:#fafbfc}.domain{font-weight:800}.meta{font-size:13px;color:var(--muted)}.status{padding:5px 8px;border-radius:999px;background:#eef1f7;font-size:12px;font-weight:800}button{border:0;border-radius:8px;padding:10px 14px;font:inherit;font-weight:700;cursor:pointer}.upload{background:var(--red);color:#fff}.upload:disabled{background:#c8cbd5;cursor:not-allowed}.notice{min-height:24px;font-weight:700}.empty{padding:30px;color:var(--muted)}
@media(max-width:760px){.optional{display:none}h1{font-size:30px}}
</style></head><body>
<header><div><h1>Nerdo domains</h1><p>Domain status and Markdown additions.</p></div><button id="refresh">Refresh</button></header>
<main><p id="notice" class="notice"></p><section id="panel" class="panel"><div class="empty">Loading…</div></section></main>
<script>
const panel=document.querySelector('#panel'),notice=document.querySelector('#notice');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=v=>v?new Date(v).toLocaleString():'—';
async function load(){notice.textContent='';const r=await fetch('api/domains',{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);if(!p.domains.length){panel.innerHTML='<div class="empty">No domains yet.</div>';return}panel.innerHTML=`<table><thead><tr><th>Domain</th><th>Status</th><th class="optional">Updated</th><th class="optional">Documents</th><th></th></tr></thead><tbody>${p.domains.map(x=>`<tr><td><div class="domain">${esc(x.domain)}</div><div class="meta">${esc(x.email||'')}</div></td><td><span class="status">${esc(x.status)}</span></td><td class="optional">${esc(date(x.updated_at))}</td><td class="optional">${esc(x.document_count)}</td><td><input hidden type="file" multiple accept=".md,.markdown,text/markdown" data-input="${esc(x.domain)}"><button class="upload" data-domain="${esc(x.domain)}" ${x.can_add_documents?'':'disabled title="Available after the domain is started"'}>Add documents</button></td></tr>`).join('')}</tbody></table>`}
panel.addEventListener('click',e=>{const b=e.target.closest('[data-domain]');if(!b||b.disabled)return;panel.querySelector(`[data-input="${CSS.escape(b.dataset.domain)}"]`).click()});
panel.addEventListener('change',async e=>{const i=e.target.closest('[data-input]');if(!i||!i.files.length)return;const f=new FormData();[...i.files].forEach(x=>f.append('files',x));notice.textContent=`Adding ${i.files.length} document(s)…`;const r=await fetch(`api/domains/${encodeURIComponent(i.dataset.input)}/documents`,{method:'POST',body:f,credentials:'same-origin'});const p=await r.json();notice.textContent=r.ok?`Added ${p.document_count} document(s) to ${p.domain}.`:(p.detail||`HTTP ${r.status}`);if(r.ok){i.value='';await load()}});
document.querySelector('#refresh').addEventListener('click',()=>load().catch(e=>notice.textContent=e.message));load().catch(e=>notice.textContent=e.message);
</script></body></html>'''


def install_dashboard(app: FastAPI, settings: Settings, storage: Storage) -> None:
    def page() -> HTMLResponse:
        return HTMLResponse(DASHBOARD)

    def domains() -> dict[str, Any]:
        return {"domains": _domains(settings, storage)}

    async def upload(domain: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        domain_dir = _domain_dir(normalized)
        if domain_dir is None:
            raise HTTPException(409, "The domain must be started before documents can be added.")
        if not files:
            raise HTTPException(400, "At least one Markdown file is required.")
        if len(files) > MAX_FILES:
            raise HTTPException(400, f"A maximum of {MAX_FILES} files may be uploaded at once.")

        target = domain_dir / "manual-documents"
        target.mkdir(parents=True, exist_ok=True)
        added: list[dict[str, Any]] = []
        for item in files:
            filename = _safe_filename(item.filename or "document.md")
            content = await item.read(MAX_FILE_BYTES + 1)
            await item.close()
            if len(content) > MAX_FILE_BYTES:
                raise HTTPException(413, f"{filename}: file exceeds the 2 MB limit.")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(400, f"{filename}: file must be UTF-8 text.") from exc
            final_path = target / filename
            temporary = target / f".{filename}.tmp"
            temporary.write_text(text.rstrip() + "\n", encoding="utf-8")
            temporary.replace(final_path)
            added.append({"filename": filename, "bytes": len(content)})

        return {
            "domain": normalized,
            "document_count": len(added),
            "documents": added,
            "available_immediately": True,
        }

    app.add_api_route(
        "/dashboard/", page, methods=["GET"], response_class=HTMLResponse, include_in_schema=False
    )
    app.add_api_route(
        "/dashboard/api/domains", domains, methods=["GET"], include_in_schema=False
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/documents",
        upload,
        methods=["POST"],
        include_in_schema=False,
    )
