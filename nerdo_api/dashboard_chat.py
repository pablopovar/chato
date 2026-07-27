from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from .config import Settings


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def _users_dir() -> Path:
    return Path(os.getenv("NERDO_USERS_DIR", "/app/users")).resolve()


def _normalize_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(400, "Invalid domain.") from exc
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise HTTPException(400, "Invalid domain.")
    return domain


def _domain_config(domain: str) -> tuple[Path, dict[str, Any]] | None:
    matches = sorted(_users_dir().glob(f"*/{domain}/nerdo.json"))
    if not matches:
        return None

    config_path = matches[0]
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            500,
            f"Could not read the active configuration for {domain}: {exc}",
        ) from exc
    return config_path.parent, config


def _active_domains() -> list[dict[str, Any]]:
    root = _users_dir()
    if not root.is_dir():
        return []

    domains: list[dict[str, Any]] = []
    for config_path in sorted(root.glob("*/*/nerdo.json")):
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            domain = _normalize_domain(
                str(config.get("domain") or config_path.parent.name)
            )
        except Exception:
            continue

        enabled = bool(config.get("enabled", True))
        key_present = bool(str(config.get("key") or "").strip())
        domains.append(
            {
                "domain": domain,
                "name": str(config.get("name") or domain),
                "enabled": enabled,
                "ready": enabled and key_present,
                "model": str(config.get("model") or ""),
                "document_count": len(list(config_path.parent.rglob("*.md"))),
            }
        )

    return sorted(domains, key=lambda item: item["domain"])


def _chat_timeout() -> float:
    raw = os.getenv(
        "NERDO_DASHBOARD_CHAT_TIMEOUT_SECONDS",
        os.getenv("NERDO_MODEL_TIMEOUT_SECONDS", "600"),
    )
    try:
        return max(1.0, float(raw))
    except ValueError as exc:
        raise HTTPException(
            500,
            "NERDO_DASHBOARD_CHAT_TIMEOUT_SECONDS must be numeric.",
        ) from exc


def _chat(
    settings: Settings,
    *,
    domain: str,
    question: str,
    session_id: str | None,
) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    configured = _domain_config(normalized)
    if configured is None:
        raise HTTPException(
            409,
            "The domain must be active before it can be tested.",
        )

    _domain_dir, config = configured
    if not config.get("enabled", True):
        raise HTTPException(409, "The domain is disabled.")

    key = str(config.get("key") or "").strip()
    if not key:
        raise HTTPException(
            500,
            "The domain configuration has no bot key.",
        )

    clean_question = question.strip()
    if len(clean_question) < 2:
        raise HTTPException(
            400,
            "Enter a question with at least two characters.",
        )
    if len(clean_question) > 4000:
        raise HTTPException(
            400,
            "The question exceeds the 4,000-character limit.",
        )

    request_body: dict[str, Any] = {
        "domain": normalized,
        "key": key,
        "question": clean_question,
    }
    if session_id:
        request_body["session_id"] = session_id

    started = time.perf_counter()
    try:
        response = httpx.post(
            settings.core_base_url.rstrip("/") + "/chat",
            json=request_body,
            timeout=_chat_timeout(),
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            f"Chat request timed out: {exc}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            f"Nerdo Core is unavailable: {exc}",
        ) from exc

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}

    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(
            response.status_code,
            detail or response.reason_phrase,
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            502,
            "Nerdo Core returned an invalid chat response.",
        )

    payload["elapsed_ms"] = elapsed_ms
    return payload


CHAT_DASHBOARD = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nerdo — Chat test</title>
<style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076;--soft:#eef1f7;--bg:#f3f4f7}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--navy)}
button,select,textarea{font:inherit}
header{background:var(--navy);color:#fff;padding:22px 5vw}
.header-row{display:flex;justify-content:space-between;gap:20px;align-items:end}
h1{font-size:38px;margin:0}
header p{margin:6px 0 0;color:#d8dbea}
header a{color:#fff;text-decoration:none;font-weight:800}
main{padding:28px 5vw}
.shell{display:grid;grid-template-columns:minmax(270px,360px) minmax(0,1fr);min-height:680px;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden}
.controls{padding:20px;background:#fafbfc;border-right:1px solid var(--line)}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:800;margin-bottom:8px}
select,textarea{width:100%;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--navy);padding:11px}
textarea{min-height:180px;resize:vertical;margin-top:18px}
.actions{display:flex;gap:8px;margin-top:12px}
button{border:0;border-radius:8px;padding:10px 14px;font-weight:800;cursor:pointer}
.primary{background:var(--red);color:#fff}
.secondary{background:var(--soft);color:var(--navy)}
button:disabled{background:#c8cbd5;color:#fff;cursor:not-allowed}
.hint{font-size:13px;line-height:1.45;color:var(--muted);margin-top:18px}
.transcript{padding:22px;overflow:auto}
.placeholder{color:var(--muted);padding:24px 0}
.turn{margin-bottom:22px}
.question{font-weight:800;margin:0 0 9px}
.answer{white-space:pre-wrap;line-height:1.55;border:1px solid var(--line);border-radius:10px;padding:16px;background:#fff}
.error{border-color:#d42c48;background:#fff5f7}
.debug{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:10px 0}
.debug div{background:var(--soft);border-radius:8px;padding:9px}
.debug dt{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:800}
.debug dd{margin:4px 0 0;font-size:13px;font-weight:800;overflow-wrap:anywhere}
details{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-top:8px}
summary{cursor:pointer;font-weight:800}
.source{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.source:first-of-type{border-top:0}
.source-title{font-weight:800}
.source-meta{font-size:12px;color:var(--muted);overflow-wrap:anywhere}
.source pre{font:inherit;white-space:pre-wrap;line-height:1.45;margin:8px 0 0}
@media(max-width:850px){.shell{grid-template-columns:1fr}.controls{border-right:0;border-bottom:1px solid var(--line)}.debug{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>
<header>
  <div class="header-row">
    <div><h1>Nerdo chat test</h1><p>Choose an active domain and inspect retrieval and model behavior directly.</p></div>
    <a href="../">Domains</a>
  </div>
</header>
<main>
  <section class="shell">
    <form id="form" class="controls">
      <label for="domain">Active domain</label>
      <select id="domain"><option value="">Loading domains…</option></select>
      <textarea id="question" maxlength="4000" placeholder="Ask a question exactly as a visitor would."></textarea>
      <div class="actions">
        <button id="send" class="primary" type="submit">Send</button>
        <button id="reset" class="secondary" type="button">New session</button>
      </div>
      <p class="hint">The result shows answer mode, configured model, latency, session ID, retrieval scores, source paths, and excerpts.</p>
    </form>
    <div id="transcript" class="transcript"><div class="placeholder">Loading active domains…</div></div>
  </section>
</main>
<script>
const domain=document.querySelector('#domain');
const question=document.querySelector('#question');
const transcript=document.querySelector('#transcript');
const send=document.querySelector('#send');
let sessionId=null;
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
function reset(){sessionId=null;transcript.innerHTML='<div class="placeholder">New session. Send a question.</div>';question.focus()}
async function loadDomains(){
  const response=await fetch('../api/chat/domains',{credentials:'same-origin'});
  const payload=await response.json();
  if(!response.ok)throw Error(payload.detail||`HTTP ${response.status}`);
  const ready=payload.domains.filter(item=>item.ready);
  domain.innerHTML=ready.length
    ?ready.map(item=>`<option value="${esc(item.domain)}">${esc(item.domain)} · ${esc(item.model||'model not set')} · ${esc(item.document_count)} docs</option>`).join('')
    :'<option value="">No active domains available</option>';
  send.disabled=!ready.length;
  transcript.innerHTML=ready.length
    ?'<div class="placeholder">Choose a domain and send a question.</div>'
    :'<div class="placeholder">No active domain with a usable bot key was found.</div>';
}
function renderSources(sources){
  if(!sources.length)return '<details><summary>Sources (0)</summary><div class="placeholder">No sources returned.</div></details>';
  return `<details><summary>Sources (${sources.length})</summary>${sources.map(source=>`<article class="source"><div class="source-title">${esc(source.index)}. ${esc(source.title||'Source')}</div><div class="source-meta">${esc(source.path||'')} · score ${esc(source.score??'—')}</div><pre>${esc(source.excerpt||'')}</pre></article>`).join('')}</details>`;
}
document.querySelector('#reset').addEventListener('click',reset);
domain.addEventListener('change',reset);
question.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter')document.querySelector('#form').requestSubmit()});
document.querySelector('#form').addEventListener('submit',async event=>{
  event.preventDefault();
  const selected=domain.value;
  const text=question.value.trim();
  if(!selected||text.length<2)return;
  send.disabled=true;
  const pending=document.createElement('div');
  pending.className='turn';
  pending.innerHTML=`<p class="question">${esc(text)}</p><div class="placeholder">Running retrieval and model response…</div>`;
  if(transcript.querySelector('.placeholder'))transcript.innerHTML='';
  transcript.prepend(pending);
  try{
    const response=await fetch('../api/chat',{
      method:'POST',
      credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({domain:selected,question:text,session_id:sessionId})
    });
    const payload=await response.json();
    if(!response.ok)throw Error(payload.detail||`HTTP ${response.status}`);
    sessionId=payload.session_id||sessionId;
    const sources=payload.sources||[];
    pending.innerHTML=`<p class="question">${esc(text)}</p><dl class="debug"><div><dt>Mode</dt><dd>${esc(payload.mode||'—')}</dd></div><div><dt>Model</dt><dd>${esc(payload.model||'—')}</dd></div><div><dt>Latency</dt><dd>${esc(payload.elapsed_ms??'—')} ms</dd></div><div><dt>Session</dt><dd>${esc(payload.session_id||'—')}</dd></div></dl><div class="answer">${esc(payload.answer||'')}</div>${renderSources(sources)}`;
    question.value='';
  }catch(error){
    pending.innerHTML=`<p class="question">${esc(text)}</p><div class="answer error">${esc(error.message)}</div>`;
  }finally{
    send.disabled=false;
    question.focus();
  }
});
loadDomains().catch(error=>{transcript.innerHTML=`<div class="answer error">${esc(error.message)}</div>`;send.disabled=true});
</script>
</body>
</html>"""


def install_dashboard_chat(app: FastAPI, settings: Settings) -> None:
    def page() -> HTMLResponse:
        return HTMLResponse(CHAT_DASHBOARD)

    def domains() -> dict[str, Any]:
        return {"domains": _active_domains()}

    def chat(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return _chat(
            settings,
            domain=str(payload.get("domain") or ""),
            question=str(payload.get("question") or ""),
            session_id=(
                str(payload.get("session_id")).strip()
                if payload.get("session_id")
                else None
            ),
        )

    app.add_api_route(
        "/dashboard/chat/",
        page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/chat/domains",
        domains,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/chat",
        chat,
        methods=["POST"],
        include_in_schema=False,
    )
