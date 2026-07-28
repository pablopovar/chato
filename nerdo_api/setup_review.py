from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse

from . import dashboard_domain
from .config import Settings


REVIEW_CSS = r'''
.review-intro{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:16px}
.review-intro p{max-width:950px;line-height:1.55}.review-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.review-pill{background:var(--soft);padding:6px 9px;border-radius:999px;font-size:12px;font-weight:800}
.review-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
.review-report{white-space:pre-wrap;line-height:1.55;font-family:inherit;background:#fbfbfd;border:1px solid var(--line);border-radius:9px;padding:16px;min-height:560px;max-height:760px;overflow:auto}
.review-summary{width:100%;min-height:620px;font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}
.crawl-table{width:100%;border-collapse:collapse;font-size:13px}.crawl-table th,.crawl-table td{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}.crawl-table th{position:sticky;top:0;background:#fff}.crawl-url{max-width:520px;overflow-wrap:anywhere}.review-only.hidden{display:none}
@media(max-width:900px){.review-grid{grid-template-columns:1fr}.crawl-table{display:block;overflow:auto}}
'''


REVIEW_PANELS = r'''
<section id="reviewPanel" class="panel hidden review-only">
  <div class="review-intro">
    <h2>Pre-activation review</h2>
    <p>This is the domain workspace before publication. Review Nerdo's processing record, Chato's understanding, the corpus, retrieval behavior, model, system prompt, and parameters. Activation promotes this exact reviewed state.</p>
    <div id="reviewMeta" class="review-meta"></div>
    <p id="reviewNotice" class="notice"></p>
    <div class="actions">
      <a id="reviewDownload" class="secondary" href="#">Download full setup report</a>
      <button id="reviewActivate" class="primary" type="button" disabled>Activate reviewed domain</button>
    </div>
  </div>
  <div class="review-grid">
    <section>
      <h2>Nerdo — Data Processing Report</h2>
      <p class="help">Retrieval, crawling, cleaning, conversion, deduplication, and search preparation. This report is not editable.</p>
      <pre id="reviewReport" class="review-report">Loading Nerdo's report…</pre>
    </section>
    <section>
      <h2>Chato — Corpus Summary</h2>
      <p class="help">This is the proposed <code>knowledge.md</code>. Edit unsupported, incomplete, stale, or misleading conclusions before activation.</p>
      <textarea id="reviewSummary" class="review-summary" spellcheck="false" disabled></textarea>
      <div class="actions"><button id="reviewSaveSummary" class="secondary" type="button" disabled>Save Chato summary</button></div>
    </section>
  </div>
</section>
<section id="crawlPanel" class="panel hidden review-only">
  <h2>Crawl and conversion visibility</h2>
  <p class="help">Inspect every recorded retrieval outcome, redirect, skip, no-index decision, source URL, response status, depth, and byte count.</p>
  <p id="crawlNotice" class="notice"></p>
  <div style="overflow:auto;max-height:760px"><table class="crawl-table"><thead><tr><th>Outcome</th><th>Requested / final URL</th><th>Status</th><th>Depth</th><th>Bytes</th><th>Reason</th></tr></thead><tbody id="crawlRows"></tbody></table></div>
</section>
'''


REVIEW_JS = r'''
const reviewIntakeId=new URLSearchParams(location.search).get('review');
let reviewLoaded=false;let crawlLoaded=false;
function reviewProcessingOnly(text){const marker='## Chato — Corpus Summary';const index=String(text||'').indexOf(marker);return index>=0?String(text).slice(0,index).trim():String(text||'').trim()}
function reviewEsc(value){return esc(value)}
async function loadReview(){
  if(!reviewIntakeId)return;
  const target=$('#reviewNotice');target.textContent='Loading Nerdo report and Chato corpus summary…';
  const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(reviewIntakeId)}`,{credentials:'same-origin'});
  const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);
  const i=p.intake||{};reviewLoaded=true;
  $('#title').textContent=`Review ${i.domain||domain}`;
  $('#reviewMeta').innerHTML=`<span class="review-pill">${reviewEsc(i.status||'unknown')}</span><span class="review-pill">${reviewEsc(i.document_count||0)} documents</span><span class="review-pill">${reviewEsc(i.duplicate_count||0)} duplicates</span><span class="review-pill">${reviewEsc(i.chunk_count||0)} search passages</span><span class="review-pill">${reviewEsc(i.email||'')}</span>`;
  $('#reviewReport').textContent=reviewProcessingOnly(p.report)||'Nerdo did not produce a processing report.';
  $('#reviewSummary').value=p.summary||'';
  $('#reviewDownload').href=`/dashboard/api/reviews/${encodeURIComponent(reviewIntakeId)}/download`;
  const workspaceReady=i.status==='awaiting_review'&&Boolean(p.report)&&Boolean(p.workspace?.ready);
  const activationReady=workspaceReady&&Boolean(p.summary);
  $('#reviewSummary').disabled=!workspaceReady;$('#reviewSaveSummary').disabled=!workspaceReady;$('#reviewActivate').disabled=!activationReady;
  if(p.summary_error){
    target.textContent=`The workspace is open, but Chato's summary generation failed: ${p.summary_error} You may write and save the summary manually, or refresh this page to retry generation.`;
  }else if(activationReady){
    target.textContent='Review every tab. Save changes in each area before activation.';
  }else{
    target.textContent=`The workspace is open, but Chato's summary is not complete. Current status: ${i.status||'unknown'}.`;
  }
}
async function loadCrawl(){
  if(!reviewIntakeId||crawlLoaded)return;crawlLoaded=true;$('#crawlNotice').textContent='Loading crawl records…';
  const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(reviewIntakeId)}/crawl`,{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);
  const rows=p.pages||[];$('#crawlRows').innerHTML=rows.map(x=>`<tr><td>${reviewEsc(x.outcome||'')}</td><td class="crawl-url"><strong>${reviewEsc(x.requested_url||'')}</strong>${x.final_url&&x.final_url!==x.requested_url?`<br>${reviewEsc(x.final_url)}`:''}</td><td>${reviewEsc(x.status_code??'—')}</td><td>${reviewEsc(x.depth??'—')}</td><td>${reviewEsc(x.bytes_read??0)}</td><td>${reviewEsc(x.skip_reason||'')}</td></tr>`).join('')||'<tr><td colspan="6">No crawl records were found.</td></tr>';
  const crawl=p.crawl||{};$('#crawlNotice').textContent=`${rows.length} recorded URL outcomes · ${crawl.attempts||0} attempts · stop reason: ${crawl.stop_reason||'not recorded'}.`;
}
$('#reviewSaveSummary').addEventListener('click',async()=>{const b=$('#reviewSaveSummary');b.disabled=true;$('#reviewNotice').textContent='Saving Chato summary…';try{const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(reviewIntakeId)}/summary`,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:$('#reviewSummary').value})});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);$('#reviewNotice').textContent=`Chato summary saved ${p.saved_at||''}.`;await loadReview()}catch(e){$('#reviewNotice').textContent=e.message;b.disabled=false}});
$('#reviewActivate').addEventListener('click',async()=>{const b=$('#reviewActivate');b.disabled=true;$('#reviewNotice').textContent='Activating the reviewed workspace…';try{const r=await fetch(`/dashboard/api/reviews/${encodeURIComponent(reviewIntakeId)}/activate`,{method:'POST',credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);location.href=`/dashboard/${encodeURIComponent(domain)}`}catch(e){$('#reviewNotice').textContent=e.message;b.disabled=false}});
if(reviewIntakeId){document.querySelectorAll('.review-only').forEach(x=>x.classList.remove('hidden'));loadReview().then(()=>setTab('review')).catch(e=>{$('#reviewNotice').textContent=e.message;setTab('review')})}
'''


def enhance_root_page(page: str) -> str:
    old_header = "Select a domain to configure it and review its conversations."
    page = page.replace(
        old_header,
        "Select an active domain or open a complete pre-activation workspace.",
        1,
    )
    start = page.find("async function load(){")
    end = page.find("\nload().catch", start)
    if start < 0 or end < 0:
        raise RuntimeError("Dashboard domain-list script marker was not found.")
    replacement = r'''async function load(){const r=await fetch('/dashboard/api/domains',{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);if(!p.domains.length){root.innerHTML='<div class="empty">No domains or intakes found.</div>';return}root.innerHTML=p.domains.map(x=>{const href=x.href||`/dashboard/${encodeURIComponent(x.domain)}`;const status=x.status||(x.enabled?'active':'disabled');const model=x.model?`<span class="pill">${esc(x.model)}</span>`:'';return `<a class="card" href="${esc(href)}"><div class="domain">${esc(x.domain)}</div><div class="name">${esc(x.name||x.domain)}</div><div class="meta"><span class="pill">${esc(status)}</span>${model}<span class="pill">${esc(x.document_count||0)} documents</span></div></a>`}).join('')}'''
    return page[:start] + replacement + page[end:]


def enhance_domain_page(page: str) -> str:
    if "reviewPanel" in page:
        return page
    required = (
        "</style></head>",
        '<div class="tabs">',
        '<section id="configPanel"',
        "$('#configPanel').classList.toggle('hidden',tab!=='config');",
        "if(tab==='history'&&!historyLoaded)loadHistory()",
        "</script></body></html>",
    )
    if any(item not in page for item in required):
        raise RuntimeError("Could not install the pre-activation workspace into the domain dashboard.")
    page = page.replace("</style></head>", REVIEW_CSS + "</style></head>", 1)
    page = page.replace(
        '<div class="tabs">',
        '<div class="tabs"><button class="review-only hidden" data-tab="review">Review</button><button class="review-only hidden" data-tab="crawl">Crawl</button>',
        1,
    )
    page = page.replace('<section id="configPanel"', REVIEW_PANELS + '<section id="configPanel"', 1)
    page = page.replace(
        "$('#configPanel').classList.toggle('hidden',tab!=='config');",
        "$('#reviewPanel').classList.toggle('hidden',tab!=='review');$('#crawlPanel').classList.toggle('hidden',tab!=='crawl');$('#configPanel').classList.toggle('hidden',tab!=='config');",
        1,
    )
    page = page.replace(
        "if(tab==='history'&&!historyLoaded)loadHistory()",
        "if(tab==='crawl'&&reviewIntakeId&&!crawlLoaded)loadCrawl().catch(e=>$('#crawlNotice').textContent=e.message);if(tab==='history'&&!historyLoaded)loadHistory()",
        1,
    )
    page = page.replace("</script></body></html>", REVIEW_JS + "</script></body></html>", 1)
    return page


def _core_response(settings: Settings, method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}))
    headers["X-Admin-Token"] = settings.core_admin_token
    try:
        response = httpx.request(
            method,
            settings.core_base_url.rstrip("/") + path,
            headers=headers,
            timeout=max(settings.request_timeout_seconds, 900),
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


def _core_json(settings: Settings, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = _core_response(settings, method, path, **kwargs)
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Nerdo Core returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(502, "Nerdo Core returned an invalid response.")
    return payload


def _operator_json(settings: Settings, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    base_url = os.getenv("NERDO_GATEWAY_BASE_URL", "http://127.0.0.1:3400").rstrip("/")
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
            rows[domain]["document_count"] = int(intake.get("document_count") or rows[domain].get("document_count") or 0)
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
        return _core_json(settings, "GET", f"/admin/intakes/{intake_id}/review")

    def save_summary(intake_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return _core_json(
            settings,
            "PUT",
            f"/admin/intakes/{intake_id}/review-summary",
            json={"content": str(payload.get("content") or "")},
        )

    def activate(intake_id: str) -> dict[str, Any]:
        review = review_data(intake_id)
        intake = review.get("intake") or {}
        domain = str(intake.get("domain") or "").strip()
        if not domain:
            raise HTTPException(409, "The intake has no domain.")
        if intake.get("status") != "awaiting_review":
            raise HTTPException(409, f"The domain cannot be activated while status is {intake.get('status')}.")
        return _operator_json(settings, "POST", f"/v1/admin/domains/{domain}/activate")

    def download(intake_id: str) -> PlainTextResponse:
        response = _core_response(settings, "GET", f"/admin/intakes/{intake_id}/setup-report")
        disposition = response.headers.get("content-disposition", 'attachment; filename="website-setup-report.md"')
        return PlainTextResponse(
            response.text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": disposition, "Cache-Control": "private, no-store"},
        )

    def crawl(intake_id: str) -> dict[str, Any]:
        dataset = _core_json(settings, "GET", f"/admin/intakes/{intake_id}/dataset")
        pages = _core_json(settings, "GET", f"/admin/intakes/{intake_id}/dataset/pages")
        return {
            "intake_id": intake_id,
            "status": dataset.get("status"),
            "dataset": dataset.get("dataset"),
            "crawl": dataset.get("crawl"),
            "fts5_enabled": dataset.get("fts5_enabled"),
            "pages": pages.get("pages", []),
        }

    def review_entry(intake_id: str) -> RedirectResponse:
        prepared = _core_json(
            settings,
            "POST",
            f"/admin/intakes/{intake_id}/review-workspace",
        )
        intake = prepared.get("intake") or {}
        domain = str(intake.get("domain") or "").strip()
        if not domain:
            raise HTTPException(409, "The intake has no domain.")
        target = f"/dashboard/{quote(domain, safe='')}?review={quote(intake_id, safe='')}"
        return RedirectResponse(target, status_code=303)

    app.add_api_route("/dashboard/api/domains", domains, methods=["GET"], include_in_schema=False)
    app.add_api_route("/dashboard/api/reviews/{intake_id}", review_data, methods=["GET"], include_in_schema=False)
    app.add_api_route("/dashboard/api/reviews/{intake_id}/summary", save_summary, methods=["PUT"], include_in_schema=False)
    app.add_api_route("/dashboard/api/reviews/{intake_id}/activate", activate, methods=["POST"], include_in_schema=False)
    app.add_api_route("/dashboard/api/reviews/{intake_id}/download", download, methods=["GET"], include_in_schema=False)
    app.add_api_route("/dashboard/api/reviews/{intake_id}/crawl", crawl, methods=["GET"], include_in_schema=False)
    app.add_api_route("/dashboard/reviews/{intake_id}", review_entry, methods=["GET"], include_in_schema=False)
    app.state.setup_review_installed = True
