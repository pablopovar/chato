from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .config import Settings


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
MAX_SYSTEM_PROMPT_CHARS = 20_000


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


def _config_path(domain: str) -> Path:
    normalized = _normalize_domain(domain)
    matches = sorted(_users_dir().glob(f"*/{normalized}/nerdo.json"))
    if not matches:
        raise HTTPException(404, f"No active configuration was found for {normalized}.")
    return matches[0]


def _read_config(domain: str) -> tuple[Path, dict[str, Any]]:
    path = _config_path(domain)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(500, f"{path} does not contain a JSON object.")
    return path, raw


def _safe_configuration(domain: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "domain": domain,
        "name": str(raw.get("name") or domain),
        "enabled": bool(raw.get("enabled", True)),
        "model": str(raw.get("model") or ""),
        "model_base_url": str(raw.get("model_base_url") or ""),
        "system_prompt": str(raw.get("system_prompt") or ""),
        "temperature": float(raw.get("temperature", 0.1)),
        "max_tokens": int(raw.get("max_tokens", 900)),
        "max_results": int(raw.get("max_results", 6)),
        "max_context_chars": int(raw.get("max_context_chars", 18_000)),
        "document_count": len(list(path.parent.rglob("*.md"))) if (path := _config_path(domain)) else 0,
    }


def _domain_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_users_dir().glob("*/*/nerdo.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            domain = _normalize_domain(str(raw.get("domain") or path.parent.name))
        except Exception:
            continue
        rows.append(
            {
                "domain": domain,
                "name": str(raw.get("name") or domain),
                "enabled": bool(raw.get("enabled", True)),
                "model": str(raw.get("model") or ""),
                "document_count": len(list(path.parent.rglob("*.md"))),
            }
        )
    return sorted(rows, key=lambda row: row["domain"])


def _available_models(raw: dict[str, Any], settings: Settings) -> tuple[list[str], str | None]:
    base_url = str(raw.get("model_base_url") or "").rstrip("/")
    api_key = str(raw.get("model_api_key") or "")
    current = str(raw.get("model") or "").strip()
    models: set[str] = {current} if current else set()
    if not base_url:
        return sorted(models), "No model base URL is configured."

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = httpx.get(
            base_url + "/models",
            headers=headers,
            timeout=min(30.0, settings.request_timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("data", []) if isinstance(payload, dict) else []
        for item in records:
            if isinstance(item, dict) and str(item.get("id") or "").strip():
                models.add(str(item["id"]).strip())
    except Exception as exc:
        return sorted(models), f"Could not list models from {base_url}: {exc}"
    return sorted(models), None


def _bounded_int(payload: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(key))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{key} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise HTTPException(400, f"{key} must be between {minimum} and {maximum}.")
    return value


def _validated_update(payload: dict[str, Any]) -> dict[str, Any]:
    model = str(payload.get("model") or "").strip()
    if not model or len(model) > 300:
        raise HTTPException(400, "model is required and must be 300 characters or fewer.")

    system_prompt = str(payload.get("system_prompt") or "").strip()
    if not system_prompt:
        raise HTTPException(400, "system_prompt is required.")
    if len(system_prompt) > MAX_SYSTEM_PROMPT_CHARS:
        raise HTTPException(
            400,
            f"system_prompt must be {MAX_SYSTEM_PROMPT_CHARS:,} characters or fewer.",
        )

    try:
        temperature = float(payload.get("temperature"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "temperature must be numeric.") from exc
    if not 0.0 <= temperature <= 2.0:
        raise HTTPException(400, "temperature must be between 0 and 2.")

    return {
        "model": model,
        "system_prompt": system_prompt,
        "temperature": temperature,
        "max_tokens": _bounded_int(payload, "max_tokens", 64, 8192),
        "max_results": _bounded_int(payload, "max_results", 1, 12),
        "max_context_chars": _bounded_int(
            payload,
            "max_context_chars",
            2000,
            100_000,
        ),
    }


def _save_configuration(domain: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    path, raw = _read_config(normalized)
    raw.update(_validated_update(payload))

    backup = path.with_name("nerdo.json.bak")
    temporary = path.with_name(".nerdo.json.tmp")
    try:
        shutil.copy2(path, backup)
        temporary.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not update {path}: {exc}") from exc

    return {
        **_safe_configuration(normalized, raw),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "backup": backup.name,
    }


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


def _chat_timeout() -> float:
    raw = os.getenv(
        "NERDO_DASHBOARD_CHAT_TIMEOUT_SECONDS",
        os.getenv("NERDO_MODEL_TIMEOUT_SECONDS", "600"),
    )
    try:
        return max(1.0, float(raw))
    except ValueError as exc:
        raise HTTPException(500, "NERDO_DASHBOARD_CHAT_TIMEOUT_SECONDS must be numeric.") from exc


def _chat(
    settings: Settings,
    domain: str,
    question: str,
    session_id: str | None,
) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    _path, raw = _read_config(normalized)
    if not raw.get("enabled", True):
        raise HTTPException(409, f"{normalized} is disabled.")
    key = str(raw.get("key") or "").strip()
    if not key:
        raise HTTPException(500, f"{normalized} has no bot key.")

    clean_question = question.strip()
    if not 2 <= len(clean_question) <= 4000:
        raise HTTPException(400, "question must contain between 2 and 4,000 characters.")

    body: dict[str, Any] = {
        "domain": normalized,
        "key": key,
        "question": clean_question,
    }
    if session_id:
        body["session_id"] = session_id

    started = time.perf_counter()
    try:
        response = httpx.post(
            settings.core_base_url.rstrip("/") + "/chat",
            json=body,
            timeout=_chat_timeout(),
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, f"Chat request timed out: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Nerdo Core is unavailable: {exc}") from exc

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    if not isinstance(payload, dict):
        raise HTTPException(502, "Nerdo Core returned an invalid chat response.")
    payload["elapsed_ms"] = elapsed_ms
    return payload


ROOT_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nerdo domains</title>
<style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076;--soft:#eef1f7}*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f4f7;color:var(--navy)}
header{padding:28px 5vw;background:var(--navy);color:#fff}h1{margin:0;font-size:40px}header p{margin:6px 0 0;color:#d8dbea}
main{padding:28px 5vw}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}.card{display:block;background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;color:inherit;text-decoration:none}.card:hover{border-color:var(--red)}.domain{font-size:21px;font-weight:850}.name{margin-top:4px;color:var(--muted)}.meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}.pill{background:var(--soft);padding:5px 8px;border-radius:999px;font-size:12px;font-weight:750}.empty{background:#fff;border:1px solid var(--line);border-radius:12px;padding:28px;color:var(--muted)}
</style></head><body>
<header><h1>Nerdo domains</h1><p>Select a domain to configure its model behavior and review its conversations.</p></header>
<main><div id="domains" class="grid"><div class="empty">Loading domains…</div></div></main>
<script>
const root=document.querySelector('#domains');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const r=await fetch('/dashboard/api/domains',{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);if(!p.domains.length){root.innerHTML='<div class="empty">No active domains found.</div>';return}root.innerHTML=p.domains.map(x=>`<a class="card" href="/dashboard/${encodeURIComponent(x.domain)}"><div class="domain">${esc(x.domain)}</div><div class="name">${esc(x.name)}</div><div class="meta"><span class="pill">${x.enabled?'active':'disabled'}</span><span class="pill">${esc(x.model||'model not set')}</span><span class="pill">${esc(x.document_count)} documents</span></div></a>`).join('')}
load().catch(e=>root.innerHTML=`<div class="empty">${esc(e.message)}</div>`);
</script></body></html>'''


DOMAIN_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nerdo domain dashboard</title>
<style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076;--soft:#eef1f7;--bg:#f3f4f7}*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--navy)}button,select,textarea,input{font:inherit}
header{padding:22px 5vw;background:var(--navy);color:#fff}.head{display:flex;justify-content:space-between;gap:20px;align-items:end}h1{margin:0;font-size:36px}header p{margin:6px 0 0;color:#d8dbea}header a{color:#fff;text-decoration:none;font-weight:800}
main{padding:24px 5vw}.tabs{display:flex;gap:8px;margin-bottom:14px}.tabs button{background:var(--soft);color:var(--navy)}.tabs button.active{background:var(--navy);color:#fff}.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px}.hidden{display:none}
label{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:800;margin-bottom:8px}select,textarea,input{width:100%;border:1px solid var(--line);border-radius:8px;padding:11px;color:var(--navy);background:#fff}textarea{resize:vertical}.prompt{min-height:280px}.question{min-height:130px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.wide{grid-column:1/-1}.actions{display:flex;gap:8px;margin-top:16px}button{border:0;border-radius:8px;padding:10px 14px;font-weight:800;cursor:pointer}.primary{background:var(--red);color:#fff}.secondary{background:var(--soft);color:var(--navy)}button:disabled{background:#c8cbd5;color:#fff}.notice{min-height:24px;font-weight:700}.help{font-size:13px;color:var(--muted);margin-top:7px}
.answer{white-space:pre-wrap;line-height:1.55;border:1px solid var(--line);border-radius:8px;padding:16px;margin-top:16px}.debug{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:12px}.debug div{background:var(--soft);border-radius:8px;padding:9px}.debug dt{font-size:10px;text-transform:uppercase;color:var(--muted);font-weight:800}.debug dd{margin:4px 0 0;overflow-wrap:anywhere}details{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-top:10px}.source{padding:10px 0;border-top:1px solid var(--line)}.source:first-of-type{border-top:0}.source pre{white-space:pre-wrap;font:inherit}
.history{display:grid;grid-template-columns:minmax(280px,380px) minmax(0,1fr);gap:16px}.conversation-list{border:1px solid var(--line);border-radius:10px;overflow:auto;max-height:720px}.conversation{display:block;width:100%;text-align:left;background:#fff;color:var(--navy);border-radius:0;border-bottom:1px solid var(--line);padding:13px}.conversation:hover,.conversation.active{background:var(--soft)}.conversation:last-child{border-bottom:0}.conversation time{display:block;font-size:12px;color:var(--muted);margin-top:5px}.preview{font-size:13px;color:var(--muted);margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.transcript{border:1px solid var(--line);border-radius:10px;padding:16px;min-height:300px}.message{margin-bottom:14px;padding:12px;border-radius:9px;white-space:pre-wrap;line-height:1.5}.message.user{background:var(--soft)}.message.assistant{border:1px solid var(--line)}.role{font-size:11px;text-transform:uppercase;letter-spacing:.08em;font-weight:850;color:var(--muted);margin-bottom:6px}.empty{color:var(--muted);padding:18px}
@media(max-width:820px){.grid,.debug,.history{grid-template-columns:1fr}.head{display:block}.head a{display:inline-block;margin-top:12px}}
</style></head><body>
<header><div class="head"><div><h1 id="title">Domain dashboard</h1><p>Model configuration, direct testing, and conversation history.</p></div><a href="/dashboard/">All domains</a></div></header>
<main>
<div class="tabs"><button class="active" data-tab="config">Configuration</button><button data-tab="chat">Test chat</button><button data-tab="history">Past chats</button></div>
<p id="notice" class="notice"></p>
<section id="configPanel" class="panel"><div class="grid">
<label class="wide">Default model<select id="model"></select><span id="modelHelp" class="help"></span></label>
<label class="wide">System prompt<textarea id="systemPrompt" class="prompt" maxlength="20000"></textarea></label>
<label>Temperature<input id="temperature" type="number" min="0" max="2" step="0.05"></label>
<label>Maximum output tokens<input id="maxTokens" type="number" min="64" max="8192" step="1"></label>
<label>Retrieval results<input id="maxResults" type="number" min="1" max="12" step="1"></label>
<label>Maximum context characters<input id="maxContextChars" type="number" min="2000" max="100000" step="500"></label>
</div><div class="actions"><button id="save" class="primary">Save configuration</button><button id="reload" class="secondary">Discard changes</button></div></section>
<section id="chatPanel" class="panel hidden"><label for="question">Question</label><textarea id="question" class="question" maxlength="4000" placeholder="Ask exactly as a visitor would."></textarea><div class="actions"><button id="send" class="primary">Send</button><button id="newSession" class="secondary">New session</button></div><div id="result"></div></section>
<section id="historyPanel" class="panel hidden"><div class="history"><div id="conversationList" class="conversation-list"><div class="empty">Open this tab to load conversations.</div></div><div id="transcript" class="transcript"><div class="empty">Select a conversation.</div></div></div></section>
</main>
<script>
const pathParts=location.pathname.split('/').filter(Boolean);const domain=decodeURIComponent(pathParts[pathParts.length-1]);let sessionId=null;let historyLoaded=false;
const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const notice=$('#notice');$('#title').textContent=domain;
function setTab(tab){document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));$('#configPanel').classList.toggle('hidden',tab!=='config');$('#chatPanel').classList.toggle('hidden',tab!=='chat');$('#historyPanel').classList.toggle('hidden',tab!=='history');if(tab==='history'&&!historyLoaded)loadHistory()}
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
async function loadConfig(){notice.textContent='Loading configuration…';const r=await fetch(`/dashboard/api/domains/${encodeURIComponent(domain)}/configuration`,{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);const c=p.configuration;$('#model').innerHTML=p.models.map(x=>`<option value="${esc(x)}" ${x===c.model?'selected':''}>${esc(x)}${x===c.model?' — current default':''}</option>`).join('');$('#modelHelp').textContent=p.model_error||`Provider: ${c.model_base_url||'not configured'}`;$('#systemPrompt').value=c.system_prompt;$('#temperature').value=c.temperature;$('#maxTokens').value=c.max_tokens;$('#maxResults').value=c.max_results;$('#maxContextChars').value=c.max_context_chars;notice.textContent=''}
$('#save').addEventListener('click',async()=>{notice.textContent='Saving…';const payload={model:$('#model').value,system_prompt:$('#systemPrompt').value,temperature:Number($('#temperature').value),max_tokens:Number($('#maxTokens').value),max_results:Number($('#maxResults').value),max_context_chars:Number($('#maxContextChars').value)};const r=await fetch(`/dashboard/api/domains/${encodeURIComponent(domain)}/configuration`,{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const p=await r.json();notice.textContent=r.ok?`Saved. ${p.configuration.model} is now the domain default.`:(p.detail||`HTTP ${r.status}`)});
$('#reload').addEventListener('click',()=>loadConfig().catch(e=>notice.textContent=e.message));
$('#newSession').addEventListener('click',()=>{sessionId=null;$('#result').innerHTML='';notice.textContent='New session.'});
function sources(items){return `<details><summary>Sources (${items.length})</summary>${items.map(s=>`<div class="source"><strong>${esc(s.index)}. ${esc(s.title||'Source')}</strong><div>${esc(s.path||'')} · score ${esc(s.score??'—')}</div><pre>${esc(s.excerpt||'')}</pre></div>`).join('')}</details>`}
$('#send').addEventListener('click',async()=>{const question=$('#question').value.trim();if(question.length<2)return;$('#send').disabled=true;notice.textContent='Running retrieval and model response…';try{const r=await fetch(`/dashboard/api/domains/${encodeURIComponent(domain)}/chat`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,session_id:sessionId})});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);sessionId=p.session_id||sessionId;$('#result').innerHTML=`<dl class="debug"><div><dt>Mode</dt><dd>${esc(p.mode||'—')}</dd></div><div><dt>Model</dt><dd>${esc(p.model||'—')}</dd></div><div><dt>Latency</dt><dd>${esc(p.elapsed_ms??'—')} ms</dd></div><div><dt>Session</dt><dd>${esc(p.session_id||'—')}</dd></div></dl><div class="answer">${esc(p.answer||'')}</div>${sources(p.sources||[])}`;notice.textContent='';$('#question').value='';historyLoaded=false}catch(e){notice.textContent=e.message}finally{$('#send').disabled=false}});
async function loadHistory(){historyLoaded=true;$('#conversationList').innerHTML='<div class="empty">Loading conversations…</div>';const r=await fetch(`/dashboard/api/domains/${encodeURIComponent(domain)}/conversations?limit=100`,{credentials:'same-origin'});const p=await r.json();if(!r.ok){$('#conversationList').innerHTML=`<div class="empty">${esc(p.detail||`HTTP ${r.status}`)}</div>`;return}const rows=p.conversations||[];if(!rows.length){$('#conversationList').innerHTML='<div class="empty">No conversations yet.</div>';return}$('#conversationList').innerHTML=rows.map(x=>`<button class="conversation" data-session="${esc(x.session_id)}"><strong>${esc(x.message_count)} messages</strong><time>${esc(new Date(x.updated_at).toLocaleString())}</time><div class="preview">${esc(x.last_user_message||x.last_assistant_message||'')}</div></button>`).join('')}
$('#conversationList').addEventListener('click',async e=>{const b=e.target.closest('[data-session]');if(!b)return;document.querySelectorAll('.conversation').forEach(x=>x.classList.toggle('active',x===b));$('#transcript').innerHTML='<div class="empty">Loading transcript…</div>';const r=await fetch(`/dashboard/api/domains/${encodeURIComponent(domain)}/conversations/${encodeURIComponent(b.dataset.session)}`,{credentials:'same-origin'});const p=await r.json();if(!r.ok){$('#transcript').innerHTML=`<div class="empty">${esc(p.detail||`HTTP ${r.status}`)}</div>`;return}$('#transcript').innerHTML=(p.messages||[]).map(m=>`<article class="message ${esc(m.role)}"><div class="role">${esc(m.role)} · ${esc(new Date(m.created_at).toLocaleString())}</div>${esc(m.content)}</article>`).join('')||'<div class="empty">No messages.</div>'});
loadConfig().catch(e=>notice.textContent=e.message);
</script></body></html>'''


def install_dashboard_domain(app: FastAPI, settings: Settings) -> None:
    def domains() -> dict[str, Any]:
        return {"domains": _domain_rows()}

    def configuration(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        _path, raw = _read_config(normalized)
        models, model_error = _available_models(raw, settings)
        return {
            "configuration": _safe_configuration(normalized, raw),
            "models": models,
            "model_error": model_error,
        }

    def save_configuration(
        domain: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        return {"configuration": _save_configuration(domain, payload)}

    def chat(
        domain: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        return _chat(
            settings,
            domain,
            str(payload.get("question") or ""),
            str(payload.get("session_id") or "").strip() or None,
        )

    def conversations(
        domain: str,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        _config_path(normalized)
        return _core_request(
            settings,
            "GET",
            f"/admin/domains/{normalized}/conversations",
            params={"limit": limit, "offset": offset},
        )

    def conversation(domain: str, session_id: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        _config_path(normalized)
        return _core_request(
            settings,
            "GET",
            f"/admin/domains/{normalized}/conversations/{session_id}",
        )

    def root_page() -> HTMLResponse:
        return HTMLResponse(ROOT_PAGE)

    def domain_page(domain: str) -> HTMLResponse:
        _config_path(domain)
        return HTMLResponse(DOMAIN_PAGE)

    app.add_api_route(
        "/dashboard/api/domains",
        domains,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/configuration",
        configuration,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/configuration",
        save_configuration,
        methods=["PUT"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/chat",
        chat,
        methods=["POST"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/conversations",
        conversations,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/conversations/{session_id}",
        conversation,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/",
        root_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/{domain}",
        domain_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/{domain}/",
        domain_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
