from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Cookie, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .config import Settings
from .share_sessions import COOKIE_NAME, ShareStore


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
BACKGROUND_DIRECTORY = "share-assets"
BACKGROUND_STEM = "background"
METADATA_FILENAME = "background.json"
DEFAULT_MAX_BYTES = 20_000_000


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


def _domain_directory(domain: str) -> Path:
    normalized = _normalize_domain(domain)
    matches = sorted(_users_dir().glob(f"*/{normalized}/nerdo.json"))
    if not matches:
        raise HTTPException(
            404,
            f"No active configuration was found for {normalized}.",
        )
    return matches[0].parent


def _asset_directory(domain: str) -> Path:
    return _domain_directory(domain) / BACKGROUND_DIRECTORY


def _max_bytes() -> int:
    raw = os.getenv("NERDO_SHARE_BACKGROUND_MAX_BYTES", str(DEFAULT_MAX_BYTES))
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(
            500,
            "NERDO_SHARE_BACKGROUND_MAX_BYTES must be an integer.",
        ) from exc
    return max(1_000_000, value)


def _image_type(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise HTTPException(
        400,
        "The background must be a PNG, JPEG, or WebP image.",
    )


def _background_path(domain: str) -> Path | None:
    directory = _asset_directory(domain)
    if not directory.is_dir():
        return None
    for suffix in (".png", ".jpg", ".webp"):
        candidate = directory / f"{BACKGROUND_STEM}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _metadata(domain: str) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    path = _background_path(normalized)
    if path is None:
        return {
            "domain": normalized,
            "configured": False,
            "maximum_bytes": _max_bytes(),
            "accepted_types": ["image/png", "image/jpeg", "image/webp"],
        }

    metadata_path = path.parent / METADATA_FILENAME
    metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        except (OSError, ValueError):
            metadata = {}

    stat = path.stat()
    return {
        "domain": normalized,
        "configured": True,
        "filename": str(metadata.get("filename") or path.name),
        "content_type": str(
            metadata.get("content_type")
            or {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix]
        ),
        "bytes": stat.st_size,
        "updated_at": str(
            metadata.get("updated_at")
            or datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        ),
        "maximum_bytes": _max_bytes(),
        "accepted_types": ["image/png", "image/jpeg", "image/webp"],
    }


def _store_background(domain: str, filename: str, raw: bytes) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    maximum = _max_bytes()
    if not raw:
        raise HTTPException(400, "The uploaded image is empty.")
    if len(raw) > maximum:
        raise HTTPException(
            413,
            f"The background image exceeds the {maximum:,}-byte limit.",
        )

    suffix, content_type = _image_type(raw)
    directory = _asset_directory(normalized)
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / f"{BACKGROUND_STEM}{suffix}"
    temporary = directory / f".{BACKGROUND_STEM}{suffix}.tmp"
    metadata_path = directory / METADATA_FILENAME
    metadata_temporary = directory / f".{METADATA_FILENAME}.tmp"
    updated_at = datetime.now(timezone.utc).isoformat()

    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
        for old_suffix in (".png", ".jpg", ".webp"):
            old = directory / f"{BACKGROUND_STEM}{old_suffix}"
            if old != target:
                old.unlink(missing_ok=True)

        metadata = {
            "filename": Path(filename or target.name).name,
            "content_type": content_type,
            "bytes": len(raw),
            "updated_at": updated_at,
        }
        metadata_temporary.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        metadata_temporary.replace(metadata_path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        metadata_temporary.unlink(missing_ok=True)
        raise HTTPException(
            500,
            f"Could not store the shared-page background: {exc}",
        ) from exc

    return _metadata(normalized)


def _remove_background(domain: str) -> bool:
    directory = _asset_directory(domain)
    removed = False
    for suffix in (".png", ".jpg", ".webp"):
        path = directory / f"{BACKGROUND_STEM}{suffix}"
        if path.is_file():
            path.unlink()
            removed = True
    (directory / METADATA_FILENAME).unlink(missing_ok=True)
    try:
        directory.rmdir()
    except OSError:
        pass
    return removed


def _dashboard_image_response(domain: str) -> FileResponse:
    path = _background_path(domain)
    if path is None:
        raise HTTPException(404, "No shared-page background is configured.")
    content_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webp": "image/webp",
    }[path.suffix]
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store"},
    )


def enhance_dashboard_page(source: str) -> str:
    if "shareBackgroundSettings" in source:
        return source

    css = r"""
<style>
.share-bg-settings{margin-top:24px;padding-top:22px;border-top:1px solid var(--line)}
.share-bg-settings h2{margin:0 0 8px}
.share-bg-preview{position:relative;overflow:hidden;min-height:260px;margin-top:16px;border:1px solid var(--line);border-radius:12px;background:var(--soft)}
.share-bg-preview img{display:block;width:100%;height:100%;min-height:260px;max-height:440px;object-fit:cover;object-position:center top}
.share-bg-placeholder{display:grid;min-height:260px;place-items:center;padding:24px;color:var(--muted);text-align:center}
.share-bg-mock{position:absolute;top:12%;right:2.5%;bottom:5%;width:clamp(190px,28%,330px);display:flex;flex-direction:column;border:3px solid rgba(255,0,43,.82);background:rgba(255,255,255,.96);box-shadow:0 18px 55px rgba(0,4,58,.28)}
.share-bg-mock-head{min-height:54px;padding:13px;background:var(--navy);color:#fff;font-weight:850}
.share-bg-mock-body{flex:1;padding:14px;color:var(--muted)}
.share-bg-file{margin-top:14px}
.share-bg-meta{margin-top:8px;font-size:13px;color:var(--muted)}
@media(max-width:720px){.share-bg-mock{top:8%;right:4%;bottom:4%;width:48%}.share-bg-preview,.share-bg-preview img,.share-bg-placeholder{min-height:220px}}
</style>
"""

    script = r"""
<script>
(() => {
  const parts = location.pathname.split('/').filter(Boolean);
  const shareBackgroundDomain = decodeURIComponent(parts[parts.length - 1]);
  const panel = document.querySelector('#sharePanel .share-box');
  if (!panel) return;

  panel.insertAdjacentHTML('beforeend', `
    <section id="shareBackgroundSettings" class="share-bg-settings">
      <h2>Shared-page background</h2>
      <p>Upload a full-page screenshot of the domain. Shared Chato links will place the live chat over it, in the position the chat would occupy on the real website.</p>
      <p class="help">This image is visual context only. It is not indexed or added to the domain knowledge.</p>
      <div id="shareBackgroundPreview" class="share-bg-preview"><div class="share-bg-placeholder">Loading background…</div></div>
      <label class="share-bg-file">Background image
        <input id="shareBackgroundFile" type="file" accept="image/png,image/jpeg,image/webp">
      </label>
      <div class="actions">
        <button id="uploadShareBackground" class="primary" type="button">Upload or replace</button>
        <button id="removeShareBackground" class="secondary" type="button">Remove background</button>
      </div>
      <div id="shareBackgroundMeta" class="share-bg-meta"></div>
    </section>
  `);

  const preview = document.querySelector('#shareBackgroundPreview');
  const file = document.querySelector('#shareBackgroundFile');
  const meta = document.querySelector('#shareBackgroundMeta');
  const upload = document.querySelector('#uploadShareBackground');
  const remove = document.querySelector('#removeShareBackground');
  const endpoint = `/dashboard/api/domains/${encodeURIComponent(shareBackgroundDomain)}/share-background`;
  const escBg = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

  function renderBackground(payload) {
    remove.disabled = !payload.configured;
    if (!payload.configured) {
      preview.innerHTML = '<div class="share-bg-placeholder">No background image configured. Shared sessions use the standard Chato page.</div>';
      meta.textContent = `PNG, JPEG, or WebP. Maximum ${Math.round(payload.maximum_bytes / 1000000)} MB.`;
      return;
    }
    const version = encodeURIComponent(payload.updated_at || Date.now());
    preview.innerHTML = `<img src="${endpoint}/image?v=${version}" alt="Shared-page background preview"><div class="share-bg-mock"><div class="share-bg-mock-head">Chato</div><div class="share-bg-mock-body">The live shared conversation appears here.</div></div>`;
    meta.textContent = `${payload.filename} · ${Math.round(payload.bytes / 1024)} KB · updated ${new Date(payload.updated_at).toLocaleString()}`;
  }

  async function loadBackground() {
    const response = await fetch(endpoint, {credentials:'same-origin'});
    const payload = await response.json();
    if (!response.ok) throw Error(payload.detail || `HTTP ${response.status}`);
    renderBackground(payload);
  }

  upload.addEventListener('click', async () => {
    if (!file.files.length) {
      notice.textContent = 'Choose a PNG, JPEG, or WebP image first.';
      return;
    }
    upload.disabled = true;
    notice.textContent = 'Uploading shared-page background…';
    try {
      const body = new FormData();
      body.append('image', file.files[0]);
      const response = await fetch(endpoint, {method:'POST', credentials:'same-origin', body});
      const payload = await response.json();
      if (!response.ok) throw Error(payload.detail || `HTTP ${response.status}`);
      renderBackground(payload);
      file.value = '';
      notice.textContent = 'Shared-page background saved.';
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      upload.disabled = false;
    }
  });

  remove.addEventListener('click', async () => {
    remove.disabled = true;
    notice.textContent = 'Removing shared-page background…';
    try {
      const response = await fetch(endpoint, {method:'DELETE', credentials:'same-origin'});
      const payload = await response.json();
      if (!response.ok) throw Error(payload.detail || `HTTP ${response.status}`);
      renderBackground(payload);
      notice.textContent = 'Shared-page background removed.';
    } catch (error) {
      notice.textContent = error.message;
    }
  });

  loadBackground().catch(error => {
    preview.innerHTML = `<div class="share-bg-placeholder">${escBg(error.message)}</div>`;
  });
})();
</script>
"""
    return source.replace("</head>", css + "</head>").replace("</body>", script + "</body>")


def enhance_session_page(source: str) -> str:
    if "siteBackgroundImage" in source:
        return source

    css = r"""
<style>
.site-background-image{display:none}
body.with-site-background{overflow:hidden;background:#d9dce5;--share-chat-width:clamp(360px,28vw,520px);--share-chat-right:clamp(16px,2vw,34px);--share-chat-top:clamp(84px,12vh,150px)}
body.with-site-background .site-background-image{position:fixed;z-index:0;inset:0;display:block;width:100vw;height:100vh;object-fit:cover;object-position:center top}
body.with-site-background header{position:fixed;z-index:2;top:var(--share-chat-top);right:var(--share-chat-right);width:var(--share-chat-width);padding:16px 18px;border-radius:14px 14px 0 0;background:rgba(0,4,58,.97);box-shadow:0 18px 55px rgba(0,4,58,.22)}
body.with-site-background header h1{font-size:22px}
body.with-site-background header p{font-size:12px;margin-top:4px}
body.with-site-background main{position:fixed;z-index:2;top:calc(var(--share-chat-top) + 82px);right:var(--share-chat-right);bottom:24px;width:var(--share-chat-width);max-width:none;margin:0;padding:0}
body.with-site-background .chat{display:flex;height:100%;flex-direction:column;border-radius:0 0 14px 14px;background:rgba(255,255,255,.98);box-shadow:0 24px 70px rgba(0,4,58,.3)}
body.with-site-background .messages{flex:1;min-height:0;max-height:none}
body.with-site-background #status{position:absolute;right:0;bottom:-22px;margin:0;padding:3px 7px;border-radius:4px;background:rgba(255,255,255,.94);font-size:11px}
@media(max-width:760px){body.with-site-background{--share-chat-right:0px;--share-chat-top:0px;--share-chat-width:100vw}body.with-site-background .site-background-image{filter:brightness(.45)}body.with-site-background header{border-radius:0;box-shadow:none}body.with-site-background main{top:82px;bottom:0}body.with-site-background .chat{border-radius:0;box-shadow:none}}
</style>
"""
    image = '<img id="siteBackgroundImage" class="site-background-image" alt="">'
    script = r"""
<script>
(() => {
  const image = document.querySelector('#siteBackgroundImage');
  if (!image) return;
  image.addEventListener('load', () => document.body.classList.add('with-site-background'));
  image.addEventListener('error', () => image.remove());
  image.src = location.pathname.replace(/\/$/, '') + '/background';
})();
</script>
"""
    return (
        source.replace("</head>", css + "</head>")
        .replace("<body>", "<body>" + image)
        .replace("</body>", script + "</body>")
    )


def install_share_background(app: FastAPI, settings: Settings) -> None:
    store = ShareStore(settings.database_path)

    def status(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        result = _metadata(normalized)
        result["preview_url"] = (
            f"/dashboard/api/domains/{normalized}/share-background/image"
            if result["configured"]
            else None
        )
        return result

    def upload(
        domain: str,
        image: UploadFile = File(...),
    ) -> dict[str, Any]:
        maximum = _max_bytes()
        raw = image.file.read(maximum + 1)
        try:
            if len(raw) > maximum:
                raise HTTPException(
                    413,
                    f"The background image exceeds the {maximum:,}-byte limit.",
                )
            result = _store_background(domain, image.filename or "background", raw)
            result["preview_url"] = (
                f"/dashboard/api/domains/{result['domain']}/share-background/image"
            )
            return result
        finally:
            image.file.close()

    def remove(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        _remove_background(normalized)
        return _metadata(normalized)

    def dashboard_image(domain: str) -> FileResponse:
        return _dashboard_image_response(_normalize_domain(domain))

    def shared_image(
        session_id: str,
        chato_share_access: str | None = Cookie(default=None),
    ) -> FileResponse:
        record = store.verify(session_id, chato_share_access or "")
        if record is None:
            raise HTTPException(
                410,
                "This shared session is unavailable or expired.",
            )
        return _dashboard_image_response(record["domain"])

    app.add_api_route(
        "/dashboard/api/domains/{domain}/share-background",
        status,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/share-background",
        upload,
        methods=["POST"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/share-background",
        remove,
        methods=["DELETE"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/share-background/image",
        dashboard_image,
        methods=["GET"],
        response_class=FileResponse,
        include_in_schema=False,
    )
    app.add_api_route(
        "/share/session/{session_id}/background",
        shared_image,
        methods=["GET"],
        response_class=FileResponse,
        include_in_schema=False,
    )
