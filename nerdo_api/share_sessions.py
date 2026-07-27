from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
from fastapi import Body, Cookie, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse

from .config import Settings


COOKIE_NAME = "chato_share_access"
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
MIN_DURATION_HOURS = 1
MAX_DURATION_HOURS = 8760


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)


def _normalize_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid domain.") from exc
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise ValueError("Invalid domain.")
    return domain


def _users_dir() -> Path:
    return Path(os.getenv("NERDO_USERS_DIR", "/app/users")).resolve()


def _domain_config(domain: str) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    paths = sorted(_users_dir().glob(f"*/{normalized}/nerdo.json"))
    if not paths:
        raise HTTPException(404, f"No active configuration was found for {normalized}.")
    try:
        raw = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"Could not read the configuration for {normalized}: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(500, f"The configuration for {normalized} is invalid.")
    return raw


class ShareStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shared_chat_sessions (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    claim_token_hash TEXT NOT NULL UNIQUE,
                    access_token_hash TEXT UNIQUE,
                    duration_hours INTEGER NOT NULL,
                    core_session_id TEXT,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    expires_at TEXT,
                    last_used_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shared_chat_domain "
                "ON shared_chat_sessions(domain, created_at DESC)"
            )

    def create(self, domain: str, duration_hours: int) -> tuple[dict[str, Any], str]:
        normalized = _normalize_domain(domain)
        if not MIN_DURATION_HOURS <= duration_hours <= MAX_DURATION_HOURS:
            raise ValueError(
                f"duration_hours must be between {MIN_DURATION_HOURS} and {MAX_DURATION_HOURS}."
            )
        session_id = f"share_{uuid.uuid4().hex}"
        claim_token = _token()
        created_at = _iso(_utcnow())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO shared_chat_sessions (
                    id, domain, claim_token_hash, access_token_hash,
                    duration_hours, core_session_id, created_at,
                    claimed_at, expires_at, last_used_at
                ) VALUES (?, ?, ?, NULL, ?, NULL, ?, NULL, NULL, NULL)
                """,
                (
                    session_id,
                    normalized,
                    _hash(claim_token),
                    duration_hours,
                    created_at,
                ),
            )
        return self.get(session_id), claim_token

    def get(self, session_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shared_chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return dict(row)

    def claim(
        self,
        claim_token: str,
        *,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        current = now or _utcnow()
        access_token = _token()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM shared_chat_sessions WHERE claim_token_hash = ?",
                (_hash(claim_token),),
            ).fetchone()
            if row is None:
                return "invalid", None, None
            if row["claimed_at"]:
                return "used", dict(row), None
            expires_at = current + timedelta(hours=int(row["duration_hours"]))
            changed = conn.execute(
                """
                UPDATE shared_chat_sessions
                SET access_token_hash = ?, claimed_at = ?, expires_at = ?, last_used_at = ?
                WHERE id = ? AND claimed_at IS NULL
                """,
                (
                    _hash(access_token),
                    _iso(current),
                    _iso(expires_at),
                    _iso(current),
                    row["id"],
                ),
            )
            if changed.rowcount != 1:
                return "used", dict(row), None
            claimed = conn.execute(
                "SELECT * FROM shared_chat_sessions WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return "claimed", dict(claimed), access_token

    def verify(
        self,
        session_id: str,
        access_token: str,
        *,
        now: datetime | None = None,
        touch: bool = True,
    ) -> dict[str, Any] | None:
        if not access_token:
            return None
        current = now or _utcnow()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shared_chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None or not row["access_token_hash"] or not row["expires_at"]:
                return None
            if not hmac.compare_digest(row["access_token_hash"], _hash(access_token)):
                return None
            if _parse(row["expires_at"]) <= current:
                return None
            if touch:
                conn.execute(
                    "UPDATE shared_chat_sessions SET last_used_at = ? WHERE id = ?",
                    (_iso(current), session_id),
                )
        return dict(row)

    def bind_core_session(
        self,
        session_id: str,
        access_token: str,
        core_session_id: str,
    ) -> bool:
        verified = self.verify(session_id, access_token, touch=False)
        if verified is None:
            return False
        with self.connect() as conn:
            conn.execute(
                "UPDATE shared_chat_sessions SET core_session_id = ?, last_used_at = ? WHERE id = ?",
                (core_session_id, _iso(_utcnow()), session_id),
            )
        return True


def _core_request(
    settings: Settings,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = dict(kwargs.pop("headers", {}))
    headers["X-Admin-Token"] = settings.core_admin_token
    try:
        response = httpx.request(
            method,
            settings.core_base_url.rstrip("/") + path,
            headers=headers,
            timeout=settings.request_timeout_seconds,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Nerdo Core is unavailable: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    if not isinstance(payload, dict):
        raise HTTPException(502, "Nerdo Core returned an invalid response.")
    return payload


def _shared_chat(
    settings: Settings,
    record: dict[str, Any],
    question: str,
) -> dict[str, Any]:
    clean_question = question.strip()
    if not 2 <= len(clean_question) <= 4000:
        raise HTTPException(400, "question must contain between 2 and 4,000 characters.")
    config = _domain_config(record["domain"])
    if not config.get("enabled", True):
        raise HTTPException(409, "This Chato is currently disabled.")
    key = str(config.get("key") or "").strip()
    if not key:
        raise HTTPException(500, "This Chato has no usable key.")
    body: dict[str, Any] = {
        "domain": record["domain"],
        "key": key,
        "question": clean_question,
    }
    if record.get("core_session_id"):
        body["session_id"] = record["core_session_id"]
    timeout = float(
        os.getenv(
            "NERDO_SHARE_CHAT_TIMEOUT_SECONDS",
            os.getenv("NERDO_MODEL_TIMEOUT_SECONDS", "600"),
        )
    )
    try:
        response = httpx.post(
            settings.core_base_url.rstrip("/") + "/chat",
            json=body,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Chato took too long to answer.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Chato is unavailable: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    if not isinstance(payload, dict):
        raise HTTPException(502, "Chato returned an invalid response.")
    return payload


CLAIM_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Open Chato session</title><style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076}*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f4f7;color:var(--navy);display:grid;min-height:100vh;place-items:center}.card{width:min(560px,90vw);background:#fff;border:1px solid var(--line);border-radius:14px;padding:28px}h1{margin:0 0 10px}p{line-height:1.5;color:var(--muted)}.error{color:#a40020;font-weight:750}
</style></head><body><main class="card"><h1>Opening your Chato session</h1><p id="status">This link can open one session only.</p></main><script>
const status=document.querySelector('#status');
fetch(location.pathname.replace(/\/$/,'')+'/claim',{method:'POST',credentials:'same-origin'})
.then(async r=>{const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);location.replace(p.session_url)})
.catch(e=>{status.className='error';status.textContent=e.message});
</script></body></html>'''


SESSION_PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chat with Chato</title><style>
:root{--navy:#00043a;--red:#ff002b;--line:#d9dce5;--muted:#5a6076;--soft:#eef1f7}*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f3f4f7;color:var(--navy)}header{padding:22px 5vw;background:var(--navy);color:#fff}h1{margin:0;font-size:32px}header p{margin:6px 0 0;color:#d8dbea}main{padding:24px 5vw;max-width:980px;margin:0 auto}.chat{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}.messages{min-height:420px;max-height:65vh;overflow:auto;padding:20px}.message{max-width:82%;padding:12px 14px;border-radius:12px;margin-bottom:12px;white-space:pre-wrap;line-height:1.5}.user{margin-left:auto;background:var(--soft)}.assistant{border:1px solid var(--line)}.composer{border-top:1px solid var(--line);padding:14px;display:grid;grid-template-columns:1fr auto;gap:10px}textarea{width:100%;min-height:74px;resize:vertical;border:1px solid var(--line);border-radius:9px;padding:11px;font:inherit}button{border:0;border-radius:9px;background:var(--red);color:#fff;font:inherit;font-weight:800;padding:10px 18px;cursor:pointer}button:disabled{background:#c8cbd5}.empty,.status{color:var(--muted)}.error{color:#a40020;font-weight:750}@media(max-width:640px){.composer{grid-template-columns:1fr}.message{max-width:94%}}
</style></head><body><header><h1 id="title">Chato</h1><p id="expiry">Loading session…</p></header><main><section class="chat"><div id="messages" class="messages"><div class="empty">Loading conversation…</div></div><form id="form" class="composer"><textarea id="question" maxlength="4000" placeholder="Ask a question"></textarea><button id="send" type="submit">Send</button></form></section><p id="status" class="status"></p></main><script>
const parts=location.pathname.split('/').filter(Boolean);const shareId=parts[parts.length-1];const messages=document.querySelector('#messages');const question=document.querySelector('#question');const send=document.querySelector('#send');const status=document.querySelector('#status');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render(items){messages.innerHTML=items.length?items.map(x=>`<div class="message ${esc(x.role)}">${esc(x.content)}</div>`).join(''):'<div class="empty">Start the conversation.</div>';messages.scrollTop=messages.scrollHeight}
async function load(){const r=await fetch(location.pathname.replace(/\/$/,'')+'/state',{credentials:'same-origin'});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);document.querySelector('#title').textContent=`Chat with ${p.name||'Chato'}`;document.querySelector('#expiry').textContent=`Session available until ${new Date(p.expires_at).toLocaleString()}`;render(p.messages||[])}
document.querySelector('#form').addEventListener('submit',async e=>{e.preventDefault();const text=question.value.trim();if(text.length<2)return;send.disabled=true;status.textContent='Chato is answering…';try{const r=await fetch(location.pathname.replace(/\/$/,'')+'/messages',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text})});const p=await r.json();if(!r.ok)throw Error(p.detail||`HTTP ${r.status}`);const current=[...messages.querySelectorAll('.message')].map(x=>({role:x.classList.contains('user')?'user':'assistant',content:x.textContent}));current.push({role:'user',content:text},{role:'assistant',content:p.answer||''});render(current);question.value='';status.textContent=''}catch(err){status.className='error';status.textContent=err.message}finally{send.disabled=false;question.focus()}});
load().catch(e=>{messages.innerHTML=`<div class="error">${esc(e.message)}</div>`;send.disabled=true});
</script></body></html>'''


def install_share_sessions(app: FastAPI, settings: Settings) -> None:
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
        path = f"/share/session/{record['id']}"
        expires_at = _parse(record["expires_at"])
        response.set_cookie(
            key=COOKIE_NAME,
            value=access_token,
            max_age=int(record["duration_hours"]) * 3600,
            expires=expires_at,
            path=path,
            secure=settings.public_base_url.startswith("https://"),
            httponly=True,
            samesite="lax",
        )
        return {
            "session_url": path,
            "domain": record["domain"],
            "expires_at": record["expires_at"],
        }

    def session_page(
        session_id: str,
        chato_share_access: str | None = Cookie(default=None),
    ) -> HTMLResponse:
        if store.verify(session_id, chato_share_access or "") is None:
            raise HTTPException(410, "This shared session is unavailable or expired.")
        return HTMLResponse(SESSION_PAGE)

    def state(
        session_id: str,
        chato_share_access: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        record = store.verify(session_id, chato_share_access or "")
        if record is None:
            raise HTTPException(410, "This shared session is unavailable or expired.")
        config = _domain_config(record["domain"])
        messages: list[dict[str, Any]] = []
        if record.get("core_session_id"):
            history = _core_request(
                settings,
                "GET",
                f"/admin/domains/{record['domain']}/conversations/{record['core_session_id']}",
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
            raise HTTPException(410, "This shared session is unavailable or expired.")
        result = _shared_chat(settings, record, str(payload.get("question") or ""))
        core_session_id = str(result.get("session_id") or "").strip()
        if core_session_id and core_session_id != record.get("core_session_id"):
            if not store.bind_core_session(session_id, access_token, core_session_id):
                raise HTTPException(410, "This shared session is unavailable or expired.")
        return {
            "answer": str(result.get("answer") or ""),
            "session_id": core_session_id,
        }

    app.add_api_route(
        "/share/{claim_token}",
        claim_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        "/share/{claim_token}/claim",
        claim,
        methods=["POST"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/share/session/{session_id}",
        session_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        "/share/session/{session_id}/state",
        state,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/share/session/{session_id}/messages",
        message,
        methods=["POST"],
        include_in_schema=False,
    )
