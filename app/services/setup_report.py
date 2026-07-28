from __future__ import annotations

from pathlib import Path

from app.services.cleaner import CleanResult
from app.services.crawler import CrawlResult
from app.services.indexer import IndexResult


def _bytes_label(value: int) -> str:
    size = float(max(0, value))
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "bytes":
                return f"{int(size):,} {unit}"
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{int(value):,} bytes"


def build_setup_report(
    *,
    domain: str,
    website_url: str,
    owner_email: str,
    started_at: str,
    completed_at: str,
    crawl_result: CrawlResult,
    clean_result: CleanResult,
    index_result: IndexResult,
    chato_summary_path: Path,
    report_path: Path,
) -> Path:
    summary = chato_summary_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).strip()
    if not summary:
        raise RuntimeError("Chato produced an empty corpus summary.")

    lines = [
        f"# Website Setup Report: {domain}",
        "",
        "## Nerdo — Data Processing Report",
        "",
        "### Setup identity",
        "",
        f"- Domain: `{domain}`",
        f"- Submitted URL: {website_url}",
        f"- Owner email: {owner_email}",
        f"- Processing started: {started_at}",
        f"- Processing completed: {completed_at}",
        "- Result: processing completed; corpus prepared for review",
        "",
        "### Retrieval and crawling",
        "",
        f"- Fetch attempts: {crawl_result.attempts:,}",
        f"- Pages retrieved: {len(crawl_result.pages):,}",
        f"- Pages skipped: {crawl_result.skipped_pages:,}",
        f"- Data retrieved: {_bytes_label(crawl_result.total_bytes)}",
        f"- Crawl stop reason: `{crawl_result.stop_reason}`",
        f"- Crawl manifest: `{crawl_result.manifest_path}`",
        "",
        "### Cleaning and standardization",
        "",
        f"- Pages submitted to cleaning: {len(clean_result.documents):,}",
        f"- Canonical Markdown documents: {len(clean_result.canonical_documents):,}",
        f"- Duplicate documents removed: {clean_result.duplicate_count:,}",
        f"- Documents discarded as unusable: {clean_result.discarded_count:,}",
        f"- Cleaning report: `{clean_result.report_path}`",
        f"- Duplicate report: `{clean_result.duplicates_path}`",
        "",
        "### Indexing",
        "",
        f"- Documents indexed: {index_result.document_count:,}",
        f"- Search chunks created: {index_result.chunk_count:,}",
        f"- SQLite FTS5 enabled: {'yes' if index_result.fts5_enabled else 'no'}",
        f"- Corpus manifest: `{index_result.manifest_path}`",
        "",
        "### Processing limits",
        "",
        "- This report describes what Nerdo retrieved and processed; it does not claim that the crawl captured every page on the website.",
        "- Skipped, duplicate, discarded, inaccessible, no-index, or out-of-scope pages may contain information absent from the prepared corpus.",
        "- Chato's summary below is grounded only in the prepared canonical corpus.",
        "",
        "## Chato — Corpus Summary",
        "",
        summary,
        "",
        "## Review Status",
        "",
        "The website corpus and Chato summary are ready for human review. Correct missing or inaccurate information before activation.",
        "",
    ]

    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(report_path)
    return report_path
