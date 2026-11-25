from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException


MARKDOWN_SUFFIXES = {".md", ".markdown"}
SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
STORE_DIRECTORY = "documents"
REGISTRY_FILENAME = ".document-store.json"
BACKUP_DIRECTORY = ".document-backups"


def normalize_domain(value: str) -> str:
    domain = value.strip().casefold().rstrip(".")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HTTPException(400, "Invalid domain.") from exc
    if not DOMAIN_PATTERN.fullmatch(domain):
        raise HTTPException(400, "Invalid domain.")
    return domain


def domain_root(users_dir: Path, domain: str) -> Path:
    normalized = normalize_domain(domain)
    matches = sorted(users_dir.resolve().glob(f"*/{normalized}/nerdo.json"))
    if not matches:
        raise HTTPException(
            404,
            f"No active configuration was found for {normalized}.",
        )
    return matches[0].parent.resolve()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _normalized_markdown(raw: bytes, filename: str) -> bytes:
    if Path(filename).suffix.casefold() not in MARKDOWN_SUFFIXES:
        raise HTTPException(400, f"{filename} is not a Markdown document.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, f"{filename} is not valid UTF-8 Markdown.") from exc
    return text.rstrip().encode("utf-8") + b"\n"


def _safe_filename(filename: str) -> str:
    supplied = Path(filename or "document.md").name
    safe = SAFE_NAME_RE.sub("-", supplied).strip(".-") or "document.md"
    suffix = Path(safe).suffix.casefold()
    if suffix not in MARKDOWN_SUFFIXES:
        raise HTTPException(400, f"{filename} is not a Markdown document.")
    stem = Path(safe).stem[: 255 - len(suffix)] or "document"
    return stem + suffix


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
    if parts and parts[0] == STORE_DIRECTORY:
        return "Added document"
    if len(parts) >= 2 and parts[0] == "manual-documents" and parts[1] == "foundry":
        return "Legacy Foundry import"
    if parts and parts[0] == "manual-documents":
        return "Legacy manual document"
    if parts and parts[0] == "mail-imports":
        return "Legacy mail import"
    return "Generated or system document"


class DomainDocumentStore:
    """Canonical persistence boundary for one deployed domain corpus.

    Callers identify what a document is and where it came from. They do not
    choose a storage directory, collision policy, backup path, or duplicate
    policy. All newly added documents use the channel-neutral ``documents/``
    namespace while legacy paths remain readable.
    """

    def __init__(self, root: Path, domain: str | None = None) -> None:
        self.root = root.resolve()
        self.domain = normalize_domain(domain) if domain else self.root.name.casefold()
        self.documents_dir = self.root / STORE_DIRECTORY
        self.registry_path = self.root / REGISTRY_FILENAME
        self.lock_path = self.root / ".document-store.lock"
        self.backup_dir = self.root / BACKUP_DIRECTORY

    @classmethod
    def for_domain(cls, users_dir: Path, domain: str) -> "DomainDocumentStore":
        normalized = normalize_domain(domain)
        return cls(domain_root(users_dir, normalized), normalized)

    def _relative(self, value: str) -> PurePosixPath:
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

    def path(self, relative_value: str, *, must_exist: bool = True) -> tuple[Path, PurePosixPath]:
        relative = self._relative(relative_value)
        candidate = (self.root / Path(*relative.parts)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise HTTPException(400, "Document path escapes the domain directory.") from exc
        if must_exist and not candidate.is_file():
            raise HTTPException(404, "Markdown document not found.")
        return candidate, relative

    def _registry(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return {"version": 1, "documents": {}}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(500, f"Could not read document registry: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(500, "Document registry is invalid.")
        documents = payload.get("documents")
        if not isinstance(documents, dict):
            documents = {}
        return {"version": 1, "documents": documents}

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialize mutations across the Core, gateway, and mail processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDONLY, 0o666)
        with os.fdopen(descriptor, "rb") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _write_registry(self, payload: dict[str, Any]) -> None:
        temporary = self.registry_path.with_name(f".{REGISTRY_FILENAME}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.registry_path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise HTTPException(500, f"Could not update document registry: {exc}") from exc

    def record(self, path: Path, *, include_sha256: bool = False) -> dict[str, Any]:
        relative = PurePosixPath(path.relative_to(self.root).as_posix())
        stat = path.stat()
        record: dict[str, Any] = {
            "path": relative.as_posix(),
            "filename": path.name,
            "category": _category(relative),
            "bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "source_url": _frontmatter_source(path),
            "editable": True,
        }
        registry = self._registry().get("documents", {})
        metadata = registry.get(relative.as_posix()) if isinstance(registry, dict) else None
        if isinstance(metadata, dict):
            record["origin"] = metadata.get("origin")
            record["original_filename"] = metadata.get("original_filename")
            record["metadata"] = metadata.get("metadata") or {}
        if include_sha256:
            record["sha256"] = _sha256(path.read_bytes())
        return record

    def list(self) -> list[dict[str, Any]]:
        paths: set[Path] = set()
        for suffix in MARKDOWN_SUFFIXES:
            paths.update(self.root.rglob(f"*{suffix}"))
        records: list[dict[str, Any]] = []
        for path in sorted(paths):
            relative = path.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts) or not path.is_file():
                continue
            records.append(self.record(path))
        records.sort(key=lambda item: (item["category"], item["path"].casefold()))
        return records

    def read(self, relative_value: str, *, max_bytes: int) -> dict[str, Any]:
        path, _relative = self.path(relative_value)
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise HTTPException(413, "Markdown document exceeds the editor size limit.")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(409, "Markdown document is not valid UTF-8.") from exc
        return {
            **self.record(path),
            "sha256": _sha256(raw),
            "content": content,
        }

    def _write_backup(self, path: Path) -> str:
        relative = path.relative_to(self.root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = self.backup_dir / relative.parent / f"{relative.name}.{stamp}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        return backup.relative_to(self.root).as_posix()

    def save(
        self,
        relative_value: str,
        content: str,
        expected_sha256: str | None,
        *,
        max_bytes: int,
    ) -> dict[str, Any]:
        with self._locked():
            path, relative = self.path(relative_value)
            current = path.read_bytes()
            current_sha = _sha256(current)
            if expected_sha256 and expected_sha256 != current_sha:
                raise HTTPException(
                    409,
                    "The document changed after it was opened. Reload before saving.",
                )
            raw = content.rstrip().encode("utf-8") + b"\n"
            if len(raw) > max_bytes:
                raise HTTPException(413, "Edited Markdown exceeds the editor size limit.")

            backup = self._write_backup(path)
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(raw)
                temporary.replace(path)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise HTTPException(500, f"Could not save Markdown: {exc}") from exc

            registry = self._registry()
            entry = registry["documents"].get(relative.as_posix())
            if isinstance(entry, dict):
                entry["sha256"] = _sha256(raw)
                entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    self._write_registry(registry)
                except Exception:
                    restore = path.with_name(f".{path.name}.{uuid4().hex}.restore")
                    try:
                        restore.write_bytes(current)
                        restore.replace(path)
                    finally:
                        restore.unlink(missing_ok=True)
                    raise

        return {
            "document": self.record(path, include_sha256=True),
            "backup": backup,
            "available_immediately": True,
        }

    def persist_markdown(
        self,
        filename: str,
        raw: bytes,
        *,
        origin: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_name = _safe_filename(filename)
        normalized = _normalized_markdown(raw, safe_name)
        digest = _sha256(normalized)
        with self._locked():
            registry = self._registry()
            records: dict[str, Any] = registry["documents"]

            # Content identity wins over channel or filename. Re-ingesting the same
            # Markdown returns the existing canonical document instead of creating
            # another persistence decision for the caller.
            for relative_value, existing in records.items():
                if not isinstance(existing, dict) or existing.get("sha256") != digest:
                    continue
                existing_path = (self.root / relative_value).resolve()
                if existing_path.is_file():
                    return {
                        **self.record(existing_path, include_sha256=True),
                        "deduplicated": True,
                    }

            self.documents_dir.mkdir(parents=True, exist_ok=True)
            destination = self.documents_dir / safe_name
            if destination.exists():
                existing_raw = destination.read_bytes()
                if _sha256(existing_raw) == digest:
                    relative = destination.relative_to(self.root).as_posix()
                    records[relative] = {
                        "sha256": digest,
                        "origin": origin,
                        "original_filename": Path(filename).name,
                        "metadata": metadata or {},
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    self._write_registry(registry)
                    return {
                        **self.record(destination, include_sha256=True),
                        "deduplicated": True,
                    }
                suffix = destination.suffix
                stem = destination.stem[: 255 - len(suffix) - len(digest) - 2]
                destination = self.documents_dir / f"{stem}__{digest}{suffix}"

            destination_existed = destination.exists()

            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(normalized)
                temporary.replace(destination)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise HTTPException(500, f"Could not persist Markdown: {exc}") from exc

            relative = destination.relative_to(self.root).as_posix()
            records[relative] = {
                "sha256": digest,
                "origin": origin,
                "original_filename": Path(filename).name,
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self._write_registry(registry)
            except Exception:
                if not destination_existed:
                    destination.unlink(missing_ok=True)
                raise
        return {
            **self.record(destination, include_sha256=True),
            "deduplicated": False,
        }

    def deploy_knowledge(self, raw: bytes) -> dict[str, Any]:
        with self._locked():
            return self._deploy_knowledge(raw)

    def _deploy_knowledge(self, raw: bytes) -> dict[str, Any]:
        normalized = _normalized_markdown(raw, "knowledge.md")
        destination = self.root / "knowledge.md"
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(normalized)
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise HTTPException(500, f"Could not deploy knowledge document: {exc}") from exc
        return self.record(destination, include_sha256=True)

    def remove_knowledge(self) -> None:
        with self._locked():
            (self.root / "knowledge.md").unlink(missing_ok=True)

    def replace_website_pages(self, source_directory: Path | None) -> list[dict[str, Any]]:
        with self._locked():
            return self._replace_website_pages(source_directory)

    def _replace_website_pages(self, source_directory: Path | None) -> list[dict[str, Any]]:
        destination = self.root / "source-pages"
        if source_directory is None or not source_directory.is_dir():
            if destination.exists():
                shutil.rmtree(destination)
            return []

        source_root = source_directory.resolve()
        if source_root == destination.resolve():
            return [
                self.record(path, include_sha256=True)
                for path in sorted(destination.rglob("*"))
                if path.is_file() and path.suffix.casefold() in MARKDOWN_SUFFIXES
            ]

        staged = self.root / f".source-pages.{uuid4().hex}.tmp"
        previous = self.root / f".source-pages.{uuid4().hex}.bak"
        records: list[dict[str, Any]] = []
        installed = False
        try:
            staged.mkdir(parents=True, exist_ok=False)
            for source in sorted(source_root.rglob("*")):
                if not source.is_file() or source.suffix.casefold() not in MARKDOWN_SUFFIXES:
                    continue
                relative = source.relative_to(source_root)
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = _normalized_markdown(source.read_bytes(), source.name)
                target.write_bytes(raw)

            if destination.exists():
                destination.replace(previous)
            try:
                staged.replace(destination)
                installed = True
            except OSError:
                if previous.exists() and not destination.exists():
                    previous.replace(destination)
                raise
            records = [
                self.record(path, include_sha256=True)
                for path in sorted(destination.rglob("*"))
                if path.is_file() and path.suffix.casefold() in MARKDOWN_SUFFIXES
            ]
            if previous.exists():
                shutil.rmtree(previous)
        except Exception:
            if staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
            if installed and destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            if previous.exists() and not destination.exists():
                previous.replace(destination)
            raise
        return records

    def export(self, *, max_bytes: int) -> dict[str, Any]:
        total = 0
        omitted = 0
        files: list[tuple[str, bytes, str]] = []
        for document in self.list():
            path, _relative = self.path(str(document["path"]))
            raw = path.read_bytes()
            if total + len(raw) > max_bytes:
                omitted += 1
                continue
            filename = SAFE_NAME_RE.sub(
                "-", str(document["path"]).replace("/", "__")
            ).strip(".-") or "document.md"
            files.append((filename, raw, str(document["path"])))
            total += len(raw)
        return {
            "files": files,
            "omitted": omitted,
            "total_bytes": total,
            "maximum_bytes": max_bytes,
        }
