from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import settings
from app.db import execute, fetch_one, utc_now
from app.services.interpreter import interpret
from app.services.setup_report import _embedded_summary


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


def _pages_dir(intake: dict[str, Any], dataset: dict[str, Any] | None) -> Path:
    dataset_value = str(
        intake.get("dataset_path")
        or (dataset or {}).get("dataset_path")
        or ""
    ).strip()
    if not dataset_value:
        raise RuntimeError("The intake has no prepared dataset path.")
    path = Path(dataset_value) / "cleaned" / "pages"
    if not path.is_dir() or not any(path.glob("*.md")):
        raise RuntimeError("The intake has no canonical Markdown pages to review.")
    return path


def _summary_path(intake: dict[str, Any]) -> Path:
    value = str(intake.get("draft_path") or "").strip()
    return Path(value) if value else settings.data_dir / "intakes" / str(intake["id"]) / "chato-summary.md"


def _report_path(intake: dict[str, Any]) -> Path:
    value = str(intake.get("report_path") or "").strip()
    return Path(value) if value else settings.data_dir / "intakes" / str(intake["id"]) / "setup-report.md"


def _write_reconstructed_report(
    intake: dict[str, Any],
    dataset: dict[str, Any] | None,
    crawl: dict[str, Any] | None,
    summary_path: Path,
    report_path: Path,
) -> None:
    summary = summary_path.read_text(encoding="utf-8", errors="replace").strip()
    if not summary:
        raise RuntimeError("Chato produced an empty corpus summary.")

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
        "## Chato — Corpus Summary",
        "",
        _embedded_summary(summary),
        "",
        "## Review Status",
        "",
        "The website corpus, Chato summary, and initial runtime configuration are ready for review before activation.",
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
) -> Path:
    root = review_domain_dir(intake)
    root.mkdir(parents=True, exist_ok=True)

    config_path = root / "nerdo.json"
    if not config_path.is_file():
        config_path.write_text(
            json.dumps(_default_config(intake), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    knowledge_path = root / "knowledge.md"
    summary = summary_path.read_text(encoding="utf-8", errors="replace")
    if not knowledge_path.is_file():
        knowledge_path.write_text(summary.rstrip() + "\n", encoding="utf-8")

    source_pages = root / "source-pages"
    if not source_pages.is_dir():
        shutil.copytree(pages_dir, source_pages)
    return root


def ensure_review_workspace(intake_id: str) -> dict[str, Any]:
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

    # Intakes completed before setup reports existed are upgraded once on first
    # review. Chato rereads the canonical corpus rather than exposing the old
    # source inventory as its understanding.
    if not report_path.is_file():
        interpret(str(intake["domain"]), pages_dir, summary_path)
        _write_reconstructed_report(
            intake,
            dataset,
            crawl,
            summary_path,
            report_path,
        )
    elif not summary_path.is_file():
        interpret(str(intake["domain"]), pages_dir, summary_path)

    root = _ensure_staging_directory(intake, pages_dir, summary_path)
    execute(
        "UPDATE intakes SET draft_path = ?, report_path = ?, updated_at = ? WHERE id = ?",
        (str(summary_path), str(report_path), utc_now(), intake_id),
    )
    intake = fetch_one("SELECT * FROM intakes WHERE id = ?", (intake_id,)) or intake
    return {
        "intake": intake,
        "dataset": dataset,
        "crawl": crawl,
        "summary_path": summary_path,
        "report_path": report_path,
        "workspace": root,
    }


def sync_review_summary(intake_id: str, content: str) -> Path:
    workspace = ensure_review_workspace(intake_id)
    knowledge = Path(workspace["workspace"]) / "knowledge.md"
    temporary = knowledge.with_name(f".{knowledge.name}.tmp")
    temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
    temporary.replace(knowledge)
    return knowledge
