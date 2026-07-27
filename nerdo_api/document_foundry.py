from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse


DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
MARKDOWN_SUFFIXES = {".md", ".markdown"}
FOUNDRY_SOURCE_SUFFIXES = {".pdf", ".docx", ".html", ".htm", ".md", ".markdown", ".txt"}
DEFAULT_MARKDOWN_MAX_BYTES = 2_000_000
DEFAULT_SOURCE_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_IMPORT_LIMIT = 20


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
    return matches[0].parent.resolve()


def _integer_env(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(500, f"{name} must be an integer.") from exc
    return max(minimum, value)


def _markdown_max_bytes() -> int:
    return _integer_env(
        "NERDO_FOUNDRY_MARKDOWN_MAX_BYTES",
        DEFAULT_MARKDOWN_MAX_BYTES,
        10_000,
    )


def _source_max_bytes() -> int:
    return _integer_env(
        "NERDO_FOUNDRY_SOURCE_MAX_BYTES",
        DEFAULT_SOURCE_MAX_BYTES,
        1_000_000,
    )


def _import_limit() -> int:
    return _integer_env(
        "NERDO_FOUNDRY_IMPORT_MAX_FILES",
        DEFAULT_IMPORT_LIMIT,
        1,
    )


def _foundry_base_url() -> str:
    return os.getenv(
        "NERDO_DOCUMENT_FOUNDRY_BASE_URL",
        "http://host.docker.internal:3500",
    ).rstrip("/")


def _foundry_timeout() -> float:
    raw = os.getenv("NERDO_DOCUMENT_FOUNDRY_TIMEOUT_SECONDS", "600")
    try:
        return max(2.0, float(raw))
    except ValueError as exc:
        raise HTTPException(
            500,
            "NERDO_DOCUMENT_FOUNDRY_TIMEOUT_SECONDS must be numeric.",
        ) from exc


def _relative_document_path(value: str) -> PurePosixPath:
    normalized = value.strip().replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part.startswith(".") for part in relative.parts)
        or relative.suffix.casefold() not in MARKDOWN_SUFFIXES
    ):
        raise HTTPException(400, "Invalid Markdown document path.")
    return relative


def _document_path(
    domain: str,
    relative_value: str,
    *,
    must_exist: bool = True,
) -> tuple[Path, Path, PurePosixPath]:
    root = _domain_directory(domain)
    relative = _relative_document_path(relative_value)
    candidate = (root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "Document path escapes the domain directory.") from exc
    if must_exist and not candidate.is_file():
        raise HTTPException(404, "Markdown document not found.")
    return root, candidate, relative


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _frontmatter_source(path: Path) -> str | None:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:12_000]
    except OSError:
        return None
    if not head.startswith("---\n"):
        return None
    for key in ("source_url", "canonical_url", "url"):
        match = re.search(
            rf"(?mi)^{re.escape(key)}\s*:\s*[\"']?([^\"'\n]+)",
            head,
        )
        if match:
            return match.group(1).strip()
    return None


def _category(relative: PurePosixPath) -> str:
    parts = relative.parts
    if relative.name.casefold() == "knowledge.md":
        return "Initial knowledge draft"
    if parts and parts[0] == "source-pages":
        return "Website data"
    if len(parts) >= 2 and parts[0] == "manual-documents" and parts[1] == "foundry":
        return "Foundry import"
    if parts and parts[0] == "manual-documents":
        return "Manual document"
    return "Generated or system document"


def _document_record(
    root: Path,
    path: Path,
    *,
    include_sha256: bool = False,
) -> dict[str, Any]:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    stat = path.stat()
    record = {
        "path": relative.as_posix(),
        "filename": path.name,
        "category": _category(relative),
        "bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
        "source_url": _frontmatter_source(path),
        "editable": True,
    }
    if include_sha256:
        record["sha256"] = _sha256(path.read_bytes())
    return record


def _list_documents(domain: str) -> list[dict[str, Any]]:
    root = _domain_directory(domain)
    paths: set[Path] = set()
    for suffix in MARKDOWN_SUFFIXES:
        paths.update(root.rglob(f"*{suffix}"))
    records: list[dict[str, Any]] = []
    for path in sorted(paths):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not path.is_file():
            continue
        records.append(_document_record(root, path))
    records.sort(key=lambda item: (item["category"], item["path"].casefold()))
    return records


def _read_document(domain: str, relative_value: str) -> dict[str, Any]:
    root, path, _relative = _document_path(domain, relative_value)
    raw = path.read_bytes()
    if len(raw) > _markdown_max_bytes():
        raise HTTPException(413, "Markdown document exceeds the editor size limit.")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(409, "Markdown document is not valid UTF-8.") from exc
    return {
        **_document_record(root, path),
        "sha256": _sha256(raw),
        "content": content,
    }


def _write_backup(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = (
        root
        / ".document-backups"
        / relative.parent
        / f"{relative.name}.{stamp}.bak"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    return backup.relative_to(root).as_posix()


def _save_document(
    domain: str,
    relative_value: str,
    content: str,
    expected_sha256: str | None,
) -> dict[str, Any]:
    root, path, _relative = _document_path(domain, relative_value)
    current = path.read_bytes()
    current_sha = _sha256(current)
    if expected_sha256 and expected_sha256 != current_sha:
        raise HTTPException(
            409,
            "The document changed after it was opened. Reload before saving.",
        )

    raw = content.rstrip().encode("utf-8") + b"\n"
    if len(raw) > _markdown_max_bytes():
        raise HTTPException(413, "Edited Markdown exceeds the editor size limit.")

    backup = _write_backup(root, path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not save Markdown: {exc}") from exc

    return {
        "document": _document_record(root, path, include_sha256=True),
        "backup": backup,
        "available_immediately": True,
    }


def _safe_import_name(filename: str, job_id: str, target: Path) -> str:
    supplied = Path(filename or "document").name
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(supplied).stem).strip("-.")
    stem = (stem or "document")[:120]
    candidate = f"{stem}.md"
    if (target / candidate).exists():
        candidate = f"{stem}__{job_id[:8]}.md"
    return candidate


def _write_foundry_link(root: Path, folder: dict[str, Any]) -> None:
    path = root / "document-foundry.json"
    temporary = root / ".document-foundry.json.tmp"
    payload = {
        "folder_id": folder["id"],
        "folder_name": folder["name"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def _foundry_status() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=_foundry_base_url(),
            timeout=min(5.0, _foundry_timeout()),
        ) as client:
            response = await client.get("/health")
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "supported_extensions": sorted(FOUNDRY_SOURCE_SUFFIXES),
        }
    return {
        "available": True,
        "service": payload.get("service", "document-foundry")
        if isinstance(payload, dict)
        else "document-foundry",
        "version": payload.get("version") if isinstance(payload, dict) else None,
        "supported_extensions": sorted(FOUNDRY_SOURCE_SUFFIXES),
    }


async def _ensure_foundry_folder(
    client: httpx.AsyncClient,
    domain: str,
    root: Path,
) -> dict[str, Any]:
    link_path = root / "document-foundry.json"
    if link_path.is_file():
        try:
            linked = json.loads(link_path.read_text(encoding="utf-8"))
            folder_id = str(linked.get("folder_id") or "").strip()
            if folder_id:
                response = await client.get(f"/v1/folders/{folder_id}")
                if response.status_code == 200:
                    return response.json()
        except (OSError, ValueError):
            pass

    response = await client.get("/v1/folders")
    response.raise_for_status()
    folders = response.json()
    folder_name = f"{domain} — Chato corpus"
    for folder in folders if isinstance(folders, list) else []:
        if str(folder.get("name") or "") == folder_name:
            _write_foundry_link(root, folder)
            return folder

    response = await client.post("/v1/folders", json={"name": folder_name})
    response.raise_for_status()
    folder = response.json()
    _write_foundry_link(root, folder)
    return folder


async def _import_sources(
    domain: str,
    files: list[UploadFile],
) -> dict[str, Any]:
    normalized = _normalize_domain(domain)
    root = _domain_directory(normalized)
    if not files:
        raise HTTPException(400, "At least one source document is required.")
    if len(files) > _import_limit():
        raise HTTPException(
            400,
            f"A maximum of {_import_limit()} source documents may be uploaded at once.",
        )

    source_limit = _source_max_bytes()
    target = root / "manual-documents" / "foundry"
    target.mkdir(parents=True, exist_ok=True)
    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    try:
        async with httpx.AsyncClient(
            base_url=_foundry_base_url(),
            timeout=_foundry_timeout(),
        ) as client:
            folder = await _ensure_foundry_folder(client, normalized, root)
            for item in files:
                original_name = Path(item.filename or "document").name
                suffix = Path(original_name).suffix.casefold()
                if suffix not in FOUNDRY_SOURCE_SUFFIXES:
                    await item.close()
                    failures.append(
                        {
                            "filename": original_name,
                            "error": f"Unsupported source type: {suffix or 'none'}",
                        }
                    )
                    continue

                raw = await item.read(source_limit + 1)
                await item.close()
                if len(raw) > source_limit:
                    failures.append(
                        {
                            "filename": original_name,
                            "error": f"File exceeds the {source_limit:,}-byte limit.",
                        }
                    )
                    continue

                response = await client.post(
                    f"/v1/folders/{folder['id']}/jobs",
                    params={"process": "true"},
                    files={
                        "file": (
                            original_name,
                            raw,
                            item.content_type or "application/octet-stream",
                        )
                    },
                )
                if response.is_error:
                    failures.append(
                        {
                            "filename": original_name,
                            "error": response.text or response.reason_phrase,
                        }
                    )
                    continue

                job = response.json()
                if job.get("status") != "complete":
                    failures.append(
                        {
                            "filename": original_name,
                            "error": str(job.get("error") or f"Foundry status: {job.get('status')}"),
                        }
                    )
                    continue

                markdown_response = await client.get(
                    f"/v1/jobs/{job['id']}/markdown"
                )
                if markdown_response.is_error:
                    failures.append(
                        {
                            "filename": original_name,
                            "error": markdown_response.text
                            or markdown_response.reason_phrase,
                        }
                    )
                    continue

                filename = _safe_import_name(original_name, str(job["id"]), target)
                path = target / filename
                temporary = target / f".{filename}.{uuid4().hex}.tmp"
                temporary.write_bytes(markdown_response.content.rstrip() + b"\n")
                temporary.replace(path)
                imported.append(
                    {
                        **_document_record(root, path, include_sha256=True),
                        "foundry_job_id": job["id"],
                        "processor": job.get("processor"),
                        "warnings": job.get("warnings", []),
                    }
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            502,
            f"Nerdo's Document Foundry is unavailable: {exc}",
        ) from exc

    return {
        "domain": normalized,
        "imported_count": len(imported),
        "failed_count": len(failures),
        "documents": imported,
        "failures": failures,
        "available_immediately": True,
    }


def install_document_foundry(app: FastAPI) -> None:
    async def summary(domain: str) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        documents = _list_documents(normalized)
        return {
            "domain": normalized,
            "document_count": len(documents),
            "documents": documents,
            "foundry": await _foundry_status(),
            "markdown_max_bytes": _markdown_max_bytes(),
            "source_max_bytes": _source_max_bytes(),
        }

    def document(
        domain: str,
        path: str = Query(..., min_length=1),
        download: bool = Query(False),
    ) -> Any:
        normalized = _normalize_domain(domain)
        if download:
            _root, file_path, _relative = _document_path(normalized, path)
            return FileResponse(
                file_path,
                media_type="text/markdown",
                filename=file_path.name,
                headers={"Cache-Control": "private, no-store"},
            )
        return _read_document(normalized, path)

    def save_document(
        domain: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        normalized = _normalize_domain(domain)
        return _save_document(
            normalized,
            str(payload.get("path") or ""),
            str(payload.get("content") or ""),
            str(payload.get("expected_sha256") or "").strip() or None,
        )

    async def import_documents(
        domain: str,
        files: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        return await _import_sources(domain, files)

    app.add_api_route(
        "/dashboard/api/domains/{domain}/foundry",
        summary,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/foundry/document",
        document,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/foundry/document",
        save_document,
        methods=["PUT"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/dashboard/api/domains/{domain}/foundry/import",
        import_documents,
        methods=["POST"],
        include_in_schema=False,
    )
