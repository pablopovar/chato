from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

from .config import Settings
from .storage import Storage


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class DomainCreateRequest(BaseModel):
    domain: str
    email: EmailStr
    business_name: str | None = Field(default=None, max_length=200)


class ConfirmRequest(BaseModel):
    confirm: bool = False


class EnabledRequest(BaseModel):
    enabled: bool


class DocumentUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str


class DocumentsUploadRequest(BaseModel):
    files: list[DocumentUpload] = Field(default_factory=list, max_length=100)


def _normalize_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(400, "Invalid domain.") from exc
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise HTTPException(400, "Invalid domain.")
    return domain


def _operator_dependency(settings: Settings):
    def operator_auth(
        x_nerdo_key: Annotated[str | None, Header()] = None,
    ) -> None:
        if not x_nerdo_key or not hmac.compare_digest(
            x_nerdo_key,
            settings.operator_token,
        ):
            raise HTTPException(401, "A valid X-Nerdo-Key is required.")

    return operator_auth


def _core_request(
    settings: Settings,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = dict(kwargs.pop("headers", {}))
    if path.startswith("/admin/"):
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
    try:
        payload: Any = response.json()
    except ValueError:
        payload = {"detail": response.text or response.reason_phrase}
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        raise HTTPException(response.status_code, detail or response.reason_phrase)
    if not isinstance(payload, dict):
        raise HTTPException(502, "Nerdo Core returned an invalid response.")
    return payload


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _core_intakes(settings: Settings) -> list[dict[str, Any]]:
    payload = _core_request(settings, "GET", "/admin/intakes")
    rows = payload.get("intakes", [])
    return rows if isinstance(rows, list) else []


def _site_rows(storage: Storage) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM sites ORDER BY created_at DESC, id DESC"
            ).fetchall()
        ]


def _submission_rows(storage: Storage) -> list[dict[str, Any]]:
    with storage.connect() as conn:
        if not _table_exists(conn, "site_submissions"):
            return []
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM site_submissions ORDER BY created_at DESC, id DESC"
            ).fetchall()
        ]


def _config_paths(settings: Settings, domain: str) -> list[Path]:
    return sorted(settings.users_dir.glob(f"*/{domain}/nerdo.json"))


def _domain_record(
    settings: Settings,
    storage: Storage,
    domain: str,
) -> dict[str, Any] | None:
    normalized = _normalize_domain(domain)
    intakes = [
        row
        for row in _core_intakes(settings)
        if str(row.get("domain", "")).casefold().rstrip(".") == normalized
    ]
    sites = [
        row
        for row in _site_rows(storage)
        if str(row.get("domain", "")).casefold().rstrip(".") == normalized
    ]
    submissions = [
        row
        for row in _submission_rows(storage)
        if str(row.get("domain", "")).casefold().rstrip(".") == normalized
    ]
    config_paths = _config_paths(settings, normalized)
    if not intakes and not sites and not submissions and not config_paths:
        return None

    intake = intakes[0] if intakes else None
    site = sites[0] if sites else None
    submission = submissions[0] if submissions else None
    return {
        "domain": normalized,
        "status": (
            str(intake.get("status"))
            if intake
            else str(site.get("status"))
            if site
            else str(submission.get("status"))
            if submission
            else "active"
        ),
        "email": (
            str(intake.get("email"))
            if intake
            else str(site.get("email"))
            if site
            else str(submission.get("email"))
            if submission
            else ""
        ),
        "website_url": (
            str(intake.get("website_url"))
            if intake
            else str(site.get("website_url"))
            if site
            else str(submission.get("website_url"))
            if submission
            else f"https://{normalized}"
        ),
        "business_name": (
            intake.get("business_name")
            if intake
            else site.get("business_name")
            if site
            else submission.get("business_name")
            if submission
            else None
        ),
        "intake_id": str(intake.get("id") or "") if intake else "",
        "site_id": str(site.get("id") or "") if site else "",
        "submission_id": str(submission.get("id") or "") if submission else "",
        "document_count": int(intake.get("document_count") or 0) if intake else 0,
        "updated_at": (
            str(intake.get("updated_at") or "")
            if intake
            else str(site.get("updated_at") or "")
            if site
            else str(submission.get("updated_at") or "")
            if submission
            else ""
        ),
        "deployed": bool(config_paths),
    }


def _all_domain_records(settings: Settings, storage: Storage) -> list[dict[str, Any]]:
    domains = {
        str(row.get("domain", "")).casefold().rstrip(".")
        for row in [*_core_intakes(settings), *_site_rows(storage), *_submission_rows(storage)]
        if row.get("domain")
    }
    for path in settings.users_dir.glob("*/*/nerdo.json"):
        domains.add(path.parent.name.casefold().rstrip("."))
    return [
        record
        for domain in sorted(domains)
        if (record := _domain_record(settings, storage, domain)) is not None
    ]


def _latest_intake(settings: Settings, domain: str) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    for row in _core_intakes(settings):
        if str(row.get("domain", "")).casefold().rstrip(".") == normalized:
            return row
    raise HTTPException(404, f"No intake was found for {normalized}.")


def _update_sites_by_intake(
    storage: Storage,
    intake_id: str,
    *,
    status: str,
    domain: str | None = None,
    bot_key: str | None = None,
) -> None:
    with storage.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM sites WHERE intake_id = ?",
            (intake_id,),
        ).fetchall()
    for row in rows:
        changes: dict[str, Any] = {"status": status}
        if domain is not None:
            changes["domain"] = domain
        if bot_key is not None:
            changes["bot_key"] = bot_key
        storage.update_site(str(row["id"]), **changes)


def _write_enabled(settings: Settings, domain: str, enabled: bool) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    paths = _config_paths(settings, normalized)
    if not paths:
        raise HTTPException(404, f"No active domain configuration was found for {normalized}.")
    changed = 0
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Could not read {path}: {exc}") from exc
        data["enabled"] = enabled
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        changed += 1
    return {"domain": normalized, "enabled": enabled, "configurations_updated": changed}


def _document_root(settings: Settings, domain: str) -> Path:
    paths = _config_paths(settings, _normalize_domain(domain))
    if not paths:
        raise HTTPException(404, "No active domain configuration was found.")
    return paths[0].parent.resolve()


def _documents(settings: Settings, domain: str) -> tuple[Path, list[Path]]:
    root = _document_root(settings, domain)
    documents: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in {".md", ".markdown"}:
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        documents.append(resolved)
    return root, documents


def _archive_deployment(settings: Settings, domain: str, kind: str) -> list[str]:
    normalized = _normalize_domain(domain)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived: list[str] = []
    for config_path in _config_paths(settings, normalized):
        domain_dir = config_path.parent
        owner = domain_dir.parent.name
        destination = settings.users_dir / f".{kind}" / stamp / normalized / owner
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(domain_dir), str(destination))
        archived.append(str(destination))
    return archived


def _clear_gateway_domain(storage: Storage, domain: str) -> dict[str, int]:
    normalized = _normalize_domain(domain)
    counts = {"sites": 0, "submissions": 0, "shared_sessions": 0}
    with storage.connect() as conn:
        if _table_exists(conn, "shared_chat_sessions"):
            cursor = conn.execute(
                "DELETE FROM shared_chat_sessions WHERE domain = ?",
                (normalized,),
            )
            counts["shared_sessions"] = max(0, cursor.rowcount)
        if _table_exists(conn, "site_submissions"):
            cursor = conn.execute(
                "DELETE FROM site_submissions WHERE domain = ?",
                (normalized,),
            )
            counts["submissions"] = max(0, cursor.rowcount)
        cursor = conn.execute("DELETE FROM sites WHERE domain = ?", (normalized,))
        counts["sites"] = max(0, cursor.rowcount)
    return counts


def install_domain_operations(app: FastAPI, settings: Settings, storage: Storage) -> None:
    if getattr(app.state, "domain_operations_installed", False):
        return
    operator_auth = _operator_dependency(settings)
    protected = [Depends(operator_auth)]

    def list_domains() -> dict[str, Any]:
        records = _all_domain_records(settings, storage)
        return {"count": len(records), "domains": records}

    def domain_status(domain: str) -> dict[str, Any]:
        record = _domain_record(settings, storage, domain)
        if record is None:
            raise HTTPException(404, "Domain not found.")
        return record

    def add_domain(payload: DomainCreateRequest) -> dict[str, Any]:
        domain = _normalize_domain(payload.domain)
        if _domain_record(settings, storage, domain) is not None:
            raise HTTPException(409, f"{domain} already exists.")
        website_url = f"https://{domain}"
        created = _core_request(
            settings,
            "POST",
            "/intakes",
            json={
                "website_url": website_url,
                "email": str(payload.email).casefold(),
                "business_name": payload.business_name,
            },
        )
        site, _site_token = storage.create_site(
            website_url=website_url,
            email=str(payload.email).casefold(),
            business_name=payload.business_name,
            domain=domain,
            intake_id=str(created["intake_id"]),
            core_status_token=str(created["status_token"]),
            status=str(created.get("status") or "queued"),
        )
        return {
            "domain": domain,
            "email": str(payload.email).casefold(),
            "site_id": site["id"],
            "intake_id": created["intake_id"],
            "status": created.get("status", "queued"),
        }

    def start_domain(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        pending = next(
            (
                row
                for row in _submission_rows(storage)
                if str(row.get("domain", "")).casefold().rstrip(".") == normalized
                and row.get("status") == "pending_approval"
            ),
            None,
        )
        if pending is None:
            raise HTTPException(404, f"No pending approval exists for {normalized}.")
        created = add_domain(
            DomainCreateRequest(
                domain=normalized,
                email=pending["email"],
                business_name=pending.get("business_name"),
            )
        )
        with storage.connect() as conn:
            conn.execute(
                "UPDATE site_submissions SET status='started', site_id=?, updated_at=? WHERE id=?",
                (
                    created["site_id"],
                    datetime.now(timezone.utc).isoformat(),
                    pending["id"],
                ),
            )
        return created

    def activate_domain(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        intake = _latest_intake(settings, normalized)
        if intake.get("status") == "active":
            return {"domain": normalized, "status": "active", "already_active": True}
        if intake.get("status") != "awaiting_review":
            raise HTTPException(
                409,
                f"{normalized} cannot be activated while status is {intake.get('status')}.",
            )
        result = _core_request(
            settings,
            "POST",
            f"/admin/intakes/{intake['id']}/activate",
            json={},
        )
        bot = result.get("bot") if isinstance(result.get("bot"), dict) else {}
        _update_sites_by_intake(
            storage,
            str(intake["id"]),
            status="active",
            domain=normalized,
            bot_key=str(bot.get("key") or "") or None,
        )
        return {"domain": normalized, "status": "active", "bot": bot}

    def retry_domain(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        intake = _latest_intake(settings, normalized)
        result = _core_request(
            settings,
            "POST",
            f"/admin/intakes/{intake['id']}/retry",
        )
        _update_sites_by_intake(storage, str(intake["id"]), status="queued")
        return {"domain": normalized, "status": "queued", "core": result}

    def set_enabled(domain: str, payload: EnabledRequest) -> dict[str, Any]:
        return _write_enabled(settings, domain, payload.enabled)

    def remove_domain(domain: str, payload: ConfirmRequest) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        if not payload.confirm:
            raise HTTPException(400, "confirm must be true to remove a domain.")
        archived = _archive_deployment(settings, normalized, "removed")
        with storage.connect() as conn:
            conn.execute(
                "UPDATE sites SET status='removed', updated_at=? WHERE domain=?",
                (datetime.now(timezone.utc).isoformat(), normalized),
            )
            if _table_exists(conn, "shared_chat_sessions"):
                conn.execute(
                    "DELETE FROM shared_chat_sessions WHERE domain=?",
                    (normalized,),
                )
        return {"domain": normalized, "status": "removed", "archived_paths": archived}

    def reset_domain(domain: str, payload: ConfirmRequest) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        if not payload.confirm:
            raise HTTPException(400, "confirm must be true to reset a domain.")
        current = _domain_record(settings, storage, normalized)
        if current is None:
            raise HTTPException(404, "Domain not found.")
        email = str(current.get("email") or "").casefold()
        if not email or "@" not in email:
            raise HTTPException(409, "The domain has no usable owner email for a fresh intake.")
        website_url = str(current.get("website_url") or f"https://{normalized}")
        business_name = current.get("business_name")

        core_reset = _core_request(
            settings,
            "POST",
            f"/admin/domains/{normalized}/reset",
            json={"confirm": True},
        )
        archived = _archive_deployment(settings, normalized, "reset")
        cleared = _clear_gateway_domain(storage, normalized)

        created = _core_request(
            settings,
            "POST",
            "/intakes",
            json={
                "website_url": website_url,
                "email": email,
                "business_name": business_name,
            },
        )
        site, _site_token = storage.create_site(
            website_url=website_url,
            email=email,
            business_name=business_name,
            domain=normalized,
            intake_id=str(created["intake_id"]),
            core_status_token=str(created["status_token"]),
            status=str(created.get("status") or "queued"),
        )
        return {
            "domain": normalized,
            "status": created.get("status", "queued"),
            "site_id": site["id"],
            "intake_id": created["intake_id"],
            "archived_paths": archived,
            "cleared": cleared,
            "core_reset": core_reset,
        }

    def list_documents(domain: str) -> dict[str, Any]:
        root, paths = _documents(settings, domain)
        return {
            "domain": _normalize_domain(domain),
            "count": len(paths),
            "documents": [
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                }
                for path in paths
            ],
        }

    def export_documents(domain: str) -> dict[str, Any]:
        root, paths = _documents(settings, domain)
        maximum = int(os.getenv("NERDO_MAIL_ATTACH_MAX_BYTES", "20000000"))
        total = 0
        omitted = 0
        files: list[dict[str, str]] = []
        for path in paths:
            raw = path.read_bytes()
            if total + len(raw) > maximum:
                omitted += 1
                continue
            relative = path.relative_to(root).as_posix()
            filename = SAFE_NAME_RE.sub("-", relative.replace("/", "__")).strip(".-") or "document.md"
            files.append(
                {
                    "filename": filename,
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                }
            )
            total += len(raw)
        return {
            "domain": _normalize_domain(domain),
            "count": len(files),
            "omitted": omitted,
            "total_bytes": total,
            "maximum_bytes": maximum,
            "files": files,
        }

    def add_documents(domain: str, payload: DocumentsUploadRequest) -> dict[str, Any]:
        root = _document_root(settings, domain)
        if not payload.files:
            raise HTTPException(400, "At least one Markdown document is required.")
        target = root / "mail-imports"
        target.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for item in payload.files:
            name = SAFE_NAME_RE.sub("-", Path(item.filename).name).strip(".-") or "document.md"
            if Path(name).suffix.casefold() not in {".md", ".markdown"}:
                raise HTTPException(400, f"{item.filename} is not a Markdown document.")
            try:
                raw = base64.b64decode(item.content_base64, validate=True)
                raw.decode("utf-8")
            except Exception as exc:
                raise HTTPException(400, f"{item.filename} is not valid UTF-8 Markdown.") from exc
            destination = target / name
            if destination.exists():
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                destination = target / f"{destination.stem}-{stamp}{destination.suffix}"
            temporary = destination.with_name(f".{destination.name}.tmp")
            temporary.write_bytes(raw)
            temporary.replace(destination)
            saved.append(destination.relative_to(root).as_posix())
        return {
            "domain": _normalize_domain(domain),
            "count": len(saved),
            "documents": saved,
        }

    app.add_api_route(
        "/v1/admin/domains",
        list_domains,
        methods=["GET"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains",
        add_domain,
        methods=["POST"],
        dependencies=protected,
        status_code=202,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}",
        domain_status,
        methods=["GET"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/start",
        start_domain,
        methods=["POST"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/activate",
        activate_domain,
        methods=["POST"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/retry",
        retry_domain,
        methods=["POST"],
        dependencies=protected,
        status_code=202,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/enabled",
        set_enabled,
        methods=["PUT"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/remove",
        remove_domain,
        methods=["POST"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/reset",
        reset_domain,
        methods=["POST"],
        dependencies=protected,
        status_code=202,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/documents",
        list_documents,
        methods=["GET"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/documents/export",
        export_documents,
        methods=["GET"],
        dependencies=protected,
    )
    app.add_api_route(
        "/v1/admin/domains/{domain}/documents",
        add_documents,
        methods=["POST"],
        dependencies=protected,
    )
    app.state.domain_operations_installed = True
