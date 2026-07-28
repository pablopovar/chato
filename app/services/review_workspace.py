from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import settings
from app.db import execute, fetch_all, fetch_one, utc_now
from app.services.interpreter import interpret
from app.services.setup_report import (
    CHATO_SECTION,
    REVIEW_SECTION,
    _embedded_summary,
)


def _bytes_label(value: int) -> str:
    size = float(max(0, value))
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size):,} {unit}" if unit == "bytes" else f"{size:,.1f} {unit}"
        size /= 1024
    return f"{value:,} bytes"


def review_owner_slug(intake_id: str) -> str:
    return f".review-{intake_id[:12]}"


def review_domain_dir(intake: dict[str, Any]) -> Path:
    return settings.users_dir / review_owner_slug(str(intake["id"])) / str(intake["domain"])


def _dataset(intake: dict[str, Any]) -> dict[str, Any] | None:
    dataset_id = str(intake.get("dataset_version_id") or "").strip()
    if dataset_id:
        return fetch_one("SELECT * FROM dataset_versions WHERE id = ?", (dataset_id,))
    return fetch_one(
        "SELECT * FROM dataset_versions WHERE intake_id = ? ORDER BY created_at DESC LIMIT 1",
        (intake["id"],),
    )


def _crawl(dataset: dict[str, Any] | None) -> dict[str, Any] | None:
    crawl_id = str((dataset or {}).get("crawl_run_id") or "").strip()
    return fetch_one("SELECT * FROM crawl_runs WHERE id = ?", (crawl_id,)) if crawl_id else None


def _has_markdown(path: Path) -> bool:
    return path.is_dir() and any(item.is_file() for item in path.rglob("*.md"))


def _safe_page_name(value: str, fallback: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(value).name).strip(".-")
    if not name:
        name = fallback
    if Path(name).suffix.casefold() not in {".md", ".markdown"}:
        name += ".md"
    return name


def _materialize_pages_from_database(intake: dict[str, Any]) -> Path:
    target = settings.data_dir / "intakes" / str(intake["id"]) / "review-source-pages"
    if _has_markdown(target):
        return target

    rows = fetch_all(
        """
        SELECT id, status, duplicate_of, clean_path, markdown
        FROM documents
        WHERE intake_id = ?
        ORDER BY created_at, id
        """,
        (intake["id"],),
    )
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    used: set[str] = set()

    for row in rows:
        status = str(row.get("status") or "").casefold()
        if row.get("duplicate_of") or status in {"duplicate", "discarded", "rejected"}:
            continue

        clean_path = Path(str(row.get("clean_path") or ""))
        markdown = str(row.get("markdown") or "")
        if clean_path.is_file():
            raw = clean_path.read_bytes()
            proposed = clean_path.name
        elif markdown.strip():
            raw = markdown.rstrip().encode("utf-8") + b"\n"
            proposed = f"page-{str(row['id'])[:12]}.md"
        else:
            continue

        name = _safe_page_name(proposed, f"page-{str(row['id'])[:12]}.md")
        if name.casefold() in used or (target / name).exists():
            stem = Path(name).stem
            suffix = Path(name).suffix or ".md"
            name = f"{stem}-{str(row['id'])[:8]}{suffix}"
        used.add(name.casefold())
        (target / name).write_bytes(raw)
        written += 1

    if not written:
        raise RuntimeError(
            "The intake has no canonical Markdown pages in either the prepared dataset or the document database."
        )
    return target


def _pages_dir(intake: dict[str, Any], dataset: dict[str, Any] | None) -> Path:
    dataset_value = str(
        intake.get("dataset_path")
        or (dataset or {}).get("dataset_path")
        or ""
    ).strip()
    if dataset_value:
        candidate = Path(dataset_value) / "cleaned" / "pages"
        if _has_markdown(candidate):
            return candidate
    return _materialize_pages_from_database(intake)


def _summary_path(intake: dict[str, Any]) -> Path:
    value = str(intake.get("draft_path") or "").strip()
    return Path(value) if value else settings.data_dir / "intakes" / str(intake["id"]) / "chato-summary.md"


def _report_path(intake: dict[str, Any]) -> Path:
    value = str(intake.get("report_path") or "").strip()
    return Path(value) if value else settings.data_dir / "intakes" / str(intake["id"]) / "setup-report.md"


def _summary_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.is_file() else ""


def _write_reconstructed_report(
    intake: dict[str, Any],
    dataset: dict[str, Any] | None,
    crawl: dict[str, Any] | None,
    summary_path: Path,
    report_path: Path,
    *,
    summary_error: str | None = None,
    include_existing_summary: bool = True,
) -> None:
    summary = _summary_text(summary_path) if include_existing_summary else ""
    discarded = fetch_one(
        "SELECT COUNT(*) AS count FROM documents WHERE intake_id = ? AND status = 'discarded'",
        (intake["id"],),
    ) or {"count": 0}
    capability = fetch_one(
        "SELECT enabled FROM runtime_capabilities WHERE name = 'sqlite_fts5'"
    )
    fetched = int((crawl or {}).get("accepted_pages") or intake.get("fetched_page_count") or 0)
    documents = int((dataset or {}).get("document_count") or intake.get("document_count") or 0)
    duplicates = int((dataset or {}).get("duplicate_count") or intake.get("duplicate_count") or 0)
    chunks = int((dataset or {}).get("chunk_count") or intake.get("chunk_count") or 0)

    chato_section = _embedded_summary(summary) if summary else (
        "Chato has not completed the corpus summary yet."
        + (f"\n\nGeneration error: `{summary_error}`" if summary_error else "")
    )
    review_status = (
        "The website corpus, Chato summary, and initial runtime configuration are ready for review before activation."
        if summary
        else "The website corpus and runtime configuration are available for review. Chato's corpus summary must be completed before activation."
    )

    lines = [
        f"# Website Setup Report: {intake['domain']}",
        "",
        "## Nerdo — Data Processing Report",
        "",
        "### Setup identity",
        "",
        f"- Domain: `{intake['domain']}`",
        f"- Submitted URL: {intake['website_url']}",
        f"- Owner email: {intake['email']}",
        f"- Intake created: {intake['created_at']}",
        f"- Processing completed: {intake['updated_at']}",
        "- Result: corpus prepared for review",
        "",
        "### Retrieval and crawling",
        "",
        f"- Fetch attempts: {int((crawl or {}).get('attempts') or 0):,}",
        f"- Pages retrieved: {fetched:,}",
        f"- Pages skipped: {int((crawl or {}).get('skipped_pages') or 0):,}",
        f"- Data retrieved: {_bytes_label(int((crawl or {}).get('total_bytes') or 0))}",
        f"- Crawl stop reason: `{(crawl or {}).get('stop_reason') or 'not recorded'}`",
        "",
        "### Cleaning and standardization",
        "",
        f"- Pages submitted to cleaning: {fetched:,}",
        f"- Canonical Markdown documents: {documents:,}",
        f"- Duplicate documents removed: {duplicates:,}",
        f"- Documents discarded as unusable: {int(discarded.get('count') or 0):,}",
        "",
        "### Search preparation",
        "",
        f"- Documents indexed: {documents:,}",
        f"- Search passages created: {chunks:,}",
        f"- Search index ready: {'yes' if capability and capability.get('enabled') else 'filesystem index only'}",
        "",
        "### Processing limits",
        "",
        "- This report describes what Nerdo retrieved and processed; it does not claim that the crawl captured every page on the website.",
        "- Skipped, duplicate, discarded, inaccessible, no-index, or out-of-scope pages may contain information absent from the prepared corpus.",
        "- Chato's summary below is grounded only in the prepared canonical corpus.",
        "",
        CHATO_SECTION,
        "",
        chato_section,
        "",
        REVIEW_SECTION,
        "",
        review_status,
        "",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(report_path)


def _default_config(intake: dict[str, Any]) -> dict[str, Any]:
    name = str(intake.get("business_name") or intake["domain"])
    origin_parts = urlsplit(str(intake["website_url"]))
    origin = f"{origin_parts.scheme}://{origin_parts.netloc}".rstrip("/")
    return {
        "domain": intake["domain"],
        "name": name,
        "enabled": True,
        "review_only": True,
        "review_intake_id": intake["id"],
        "debug": False,
        "key": secrets.token_urlsafe(32),
        "system_prompt": (
            f"You are the website guide for {name}. "
            "Answer from the supplied knowledge. "
            "Do not invent missing information."
        ),
        "model": settings.model_name,
        "model_base_url": settings.model_base_url,
        "model_api_key": settings.model_api_key,
        "allowed_origins": [origin] if origin else [],
        "max_results": 6,
        "max_context_chars": 18_000,
        "temperature": 0.1,
        "max_tokens": 900,
        "welcome_message": f"Ask a question about {name}.",
        "suggested_questions": [],
    }


def _ensure_staging_directory(
    intake: dict[str, Any],
    pages_dir: Path,
    summary_path: Path,
    *,
    include_summary: bool = True,
) -> Path:
    root = review_domain_dir(intake)
    root.mkdir(parents=True, exist_ok=True)

    config_path = root / "nerdo.json"
    if not config_path.is_file():
        config_path.write_text(
            json.dumps(_default_config(intake), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    summary = _summary_text(summary_path) if include_summary else ""
    knowledge_path = root / "knowledge.md"
    if summary:
        if not knowledge_path.is_file():
            knowledge_path.write_text(summary.rstrip() + "\n", encoding="utf-8")
    elif not include_summary:
        knowledge_path.unlink(missing_ok=True)

    source_pages = root / "source-pages"
    if not _has_markdown(source_pages):
        if source_pages.exists():
            shutil.rmtree(source_pages)
        shutil.copytree(pages_dir, source_pages)
    return root


def prepare_review_workspace(intake_id: str) -> dict[str, Any]:
    intake = fetch_one("SELECT * FROM intakes WHERE id = ?", (intake_id,))
    if not intake:
        raise RuntimeError("Intake not found.")
    if intake.get("status") not in {"awaiting_review", "active"}:
        raise RuntimeError(
            f"The intake cannot be reviewed while status is {intake.get('status')}."
        )

    dataset = _dataset(intake)
    crawl = _crawl(dataset)
    pages_dir = _pages_dir(intake, dataset)
    summary_path = _summary_path(intake)
    report_path = _report_path(intake)

    report_valid = False
    if report_path.is_file():
        report = report_path.read_text(encoding="utf-8", errors="replace")
        report_valid = CHATO_SECTION in report and REVIEW_SECTION in report
    summary_needs_regeneration = not report_valid
    if not report_valid:
        _write_reconstructed_report(
            intake,
            dataset,
            crawl,
            summary_path,
            report_path,
            include_existing_summary=False,
        )

    root = _ensure_staging_directory(
        intake,
        pages_dir,
        summary_path,
        include_summary=not summary_needs_regeneration,
    )
    summary_ready = bool(_summary_text(summary_path)) and not summary_needs_regeneration
    draft_value = str(summary_path) if summary_ready else None
    execute(
        "UPDATE intakes SET draft_path = ?, report_path = ?, updated_at = ? WHERE id = ?",
        (draft_value, str(report_path), utc_now(), intake_id),
    )
    intake = fetch_one("SELECT * FROM intakes WHERE id = ?", (intake_id,)) or intake
    return {
        "intake": intake,
        "dataset": dataset,
        "crawl": crawl,
        "pages_dir": pages_dir,
        "summary_path": summary_path,
        "report_path": report_path,
        "workspace": root,
        "summary_ready": summary_ready,
        "summary_needs_regeneration": summary_needs_regeneration,
        "summary_error": None,
    }


def ensure_review_workspace(intake_id: str) -> dict[str, Any]:
    workspace = prepare_review_workspace(intake_id)
    summary_path = Path(workspace["summary_path"])
    report_path = Path(workspace["report_path"])
    summary_error: str | None = None
    regenerate = bool(workspace.get("summary_needs_regeneration"))

    if regenerate or not _summary_text(summary_path):
        if regenerate and summary_path.is_file():
            backup = summary_path.with_name(f"{summary_path.name}.legacy-source-inventory.bak")
            if not backup.exists():
                shutil.copy2(summary_path, backup)
            summary_path.unlink()
        try:
            interpret(
                str(workspace["intake"]["domain"]),
                Path(workspace["pages_dir"]),
                summary_path,
            )
            summary = _summary_text(summary_path)
            if not summary:
                raise RuntimeError("Chato produced an empty corpus summary.")
            knowledge = Path(workspace["workspace"]) / "knowledge.md"
            temporary = knowledge.with_name(f".{knowledge.name}.tmp")
            temporary.write_text(summary.rstrip() + "\n", encoding="utf-8")
            temporary.replace(knowledge)
            execute(
                "UPDATE intakes SET draft_path = ?, updated_at = ? WHERE id = ?",
                (str(summary_path), utc_now(), intake_id),
            )
        except Exception as exc:  # Keep the review workspace accessible.
            summary_error = f"{type(exc).__name__}: {exc}"

        _write_reconstructed_report(
            workspace["intake"],
            workspace.get("dataset"),
            workspace.get("crawl"),
            summary_path,
            report_path,
            summary_error=summary_error,
        )

    workspace["summary_ready"] = bool(_summary_text(summary_path))
    workspace["summary_needs_regeneration"] = False
    workspace["summary_error"] = summary_error
    workspace["intake"] = fetch_one("SELECT * FROM intakes WHERE id = ?", (intake_id,)) or workspace["intake"]
    return workspace


def sync_review_summary(intake_id: str, content: str) -> Path:
    workspace = prepare_review_workspace(intake_id)
    knowledge = Path(workspace["workspace"]) / "knowledge.md"
    temporary = knowledge.with_name(f".{knowledge.name}.tmp")
    temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
    temporary.replace(knowledge)
    return knowledge
