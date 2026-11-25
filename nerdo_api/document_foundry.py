from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .document_store import DomainDocumentStore, normalize_domain


FOUNDRY_SOURCE_SUFFIXES = {".pdf", ".docx", ".html", ".htm", ".md", ".markdown", ".txt"}
DEFAULT_MARKDOWN_MAX_BYTES = 2_000_000
DEFAULT_SOURCE_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_IMPORT_LIMIT = 20


def _users_dir() -> Path:
    return Path(os.getenv("NERDO_USERS_DIR", "/app/users")).resolve()


def _store(domain: str) -> DomainDocumentStore:
    return DomainDocumentStore.for_domain(_users_dir(), domain)


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


def _markdown_import_name(filename: str) -> str:
    supplied = Path(filename or "document").name
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(supplied).stem).strip("-.")
    stem = (stem or "document")[:120]
    return f"{stem}.md"


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
) -> dict[str, Any]:
    # Document Foundry is a conversion engine here, not a second domain
    # document store. One integration scratch folder is enough; Chato & Nerdo
    # owns canonical document identity and persistence after conversion.
    folder_name = os.getenv(
        "NERDO_DOCUMENT_FOUNDRY_FOLDER_NAME",
        "Chato & Nerdo conversion",
    ).strip() or "Chato & Nerdo conversion"

    response = await client.get("/v1/folders")
    response.raise_for_status()
    folders = response.json()
    for folder in folders if isinstance(folders, list) else []:
        if str(folder.get("name") or "") == folder_name:
            return folder

    response = await client.post("/v1/folders", json={"name": folder_name})
    response.raise_for_status()
    return response.json()


async def _import_sources(
    domain: str,
    files: list[UploadFile],
) -> dict[str, Any]:
    normalized = normalize_domain(domain)
    store = _store(normalized)
    if not files:
        raise HTTPException(400, "At least one source document is required.")
    if len(files) > _import_limit():
        raise HTTPException(
            400,
            f"A maximum of {_import_limit()} source documents may be uploaded at once.",
        )

    source_limit = _source_max_bytes()
    imported: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    try:
        async with httpx.AsyncClient(
            base_url=_foundry_base_url(),
            timeout=_foundry_timeout(),
        ) as client:
            folder = await _ensure_foundry_folder(client)
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
                            "error": str(
                                job.get("error")
                                or f"Foundry status: {job.get('status')}"
                            ),
                        }
                    )
                    continue

                markdown_response = await client.get(f"/v1/jobs/{job['id']}/markdown")
                if markdown_response.is_error:
                    failures.append(
                        {
                            "filename": original_name,
                            "error": markdown_response.text
                            or markdown_response.reason_phrase,
                        }
                    )
                    continue

                persisted = store.persist_markdown(
                    _markdown_import_name(original_name),
                    markdown_response.content,
                    origin="document-foundry",
                    metadata={
                        "source_filename": original_name,
                        "foundry_job_id": job.get("id"),
                        "processor": job.get("processor"),
                        "warnings": job.get("warnings", []),
                    },
                )
                imported.append(
                    {
                        **persisted,
                        "foundry_job_id": job.get("id"),
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
        normalized = normalize_domain(domain)
        store = _store(normalized)
        documents = store.list()
        return {
            "domain": normalized,
            "document_count": len(documents),
            "documents": documents,
            "foundry": await _foundry_status(),
            "markdown_max_bytes": _markdown_max_bytes(),
            "source_max_bytes": _source_max_bytes(),
            "persistence": {
                "managed": True,
                "namespace": "documents/",
                "registry": ".document-store.json",
            },
        }

    def document(
        domain: str,
        path: str = Query(..., min_length=1),
        download: bool = Query(False),
    ) -> Any:
        normalized = normalize_domain(domain)
        store = _store(normalized)
        if download:
            file_path, _relative = store.path(path)
            return FileResponse(
                file_path,
                media_type="text/markdown",
                filename=file_path.name,
                headers={"Cache-Control": "private, no-store"},
            )
        return store.read(path, max_bytes=_markdown_max_bytes())

    def save_document(
        domain: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        normalized = normalize_domain(domain)
        return _store(normalized).save(
            str(payload.get("path") or ""),
            str(payload.get("content") or ""),
            str(payload.get("expected_sha256") or "").strip() or None,
            max_bytes=_markdown_max_bytes(),
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
