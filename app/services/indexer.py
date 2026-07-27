from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.db import connection, utc_now
from app.services.cleaner import CleanResult, CleanedDocument
from app.services.crawler import CrawlResult


FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(frozen=True)
class IndexedChunk:
    id: str
    document_id: str
    ordinal: int
    title: str
    heading: str | None
    source_url: str
    text: str
    text_sha256: str
    file_path: str


@dataclass(frozen=True)
class IndexResult:
    document_count: int
    duplicate_count: int
    discarded_count: int
    chunk_count: int
    fts5_enabled: bool
    documents_path: str
    chunks_path: str
    manifest_path: str


def _split_long_text(text: str, maximum: int, overlap: int) -> list[str]:
    if len(text) <= maximum:
        return [text]

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + maximum)
        if end < len(text):
            boundary = text.rfind(" ", start + maximum // 2, end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return pieces


def _sections(markdown: str) -> list[tuple[str | None, str]]:
    markdown = FRONTMATTER_PATTERN.sub("", markdown, count=1)
    sections: list[tuple[str | None, list[str]]] = []
    heading: str | None = None
    lines: list[str] = []

    for line in markdown.splitlines():
        match = HEADING_PATTERN.match(line)
        if match:
            if lines:
                sections.append((heading, lines))
            heading = match.group(2).strip()
            lines = []
        else:
            lines.append(line)
    if lines:
        sections.append((heading, lines))

    result: list[tuple[str | None, str]] = []
    for section_heading, section_lines in sections:
        text = re.sub(
            r"\n{3,}",
            "\n\n",
            "\n".join(section_lines),
        ).strip()
        if text:
            result.append((section_heading, text))
    return result


def _document_chunks(document: CleanedDocument) -> list[IndexedChunk]:
    chunks: list[IndexedChunk] = []
    ordinal = 0

    for heading, section_text in _sections(document.markdown):
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", section_text)
            if paragraph.strip()
        ]
        current = ""

        def flush() -> None:
            nonlocal current, ordinal
            if not current.strip():
                return
            for piece in _split_long_text(
                current,
                settings.index_chunk_chars,
                settings.index_chunk_overlap,
            ):
                ordinal += 1
                chunk_id = str(uuid4())
                chunks.append(
                    IndexedChunk(
                        id=chunk_id,
                        document_id=document.id,
                        ordinal=ordinal,
                        title=document.title,
                        heading=heading,
                        source_url=document.source_url,
                        text=piece,
                        text_sha256=hashlib.sha256(
                            piece.encode("utf-8")
                        ).hexdigest(),
                        file_path=document.clean_path or "",
                    )
                )
            current = ""

        for paragraph in paragraphs:
            candidate = paragraph if not current else f"{current}\n\n{paragraph}"
            if len(candidate) <= settings.index_chunk_chars:
                current = candidate
            else:
                flush()
                current = paragraph
        flush()

    return chunks


def _jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _relative_or_absolute(path: str | None, dataset_dir: Path) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    try:
        return str(candidate.relative_to(dataset_dir))
    except ValueError:
        return str(candidate)


def build_index(
    *,
    intake_id: str,
    dataset_version_id: str,
    dataset_dir: Path,
    crawl_result: CrawlResult,
    clean_result: CleanResult,
) -> IndexResult:
    index_dir = dataset_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    documents_path = index_dir / "documents.jsonl"
    chunks_path = index_dir / "chunks.jsonl"
    manifest_path = dataset_dir / "dataset-manifest.json"

    chunks = [
        chunk
        for document in clean_result.canonical_documents
        for chunk in _document_chunks(document)
    ]

    document_rows: list[dict] = []
    for document in clean_result.documents:
        row = asdict(document)
        row["raw_path"] = _relative_or_absolute(
            document.raw_path,
            dataset_dir,
        )
        row["clean_path"] = _relative_or_absolute(
            document.clean_path,
            dataset_dir,
        )
        document_rows.append(row)

    chunk_rows = [asdict(chunk) for chunk in chunks]
    for row in chunk_rows:
        row["file_path"] = _relative_or_absolute(
            row["file_path"],
            dataset_dir,
        )

    _jsonl(documents_path, document_rows)
    _jsonl(chunks_path, chunk_rows)

    now = utc_now()
    with connection() as conn:
        capability = conn.execute(
            "SELECT enabled FROM runtime_capabilities WHERE name = 'sqlite_fts5'"
        ).fetchone()
        fts5_enabled = bool(capability and capability["enabled"])

        existing_chunk_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM chunks WHERE dataset_version_id = ?",
                (dataset_version_id,),
            ).fetchall()
        ]
        if fts5_enabled:
            for chunk_id in existing_chunk_ids:
                conn.execute(
                    "DELETE FROM chunks_fts WHERE chunk_id = ?",
                    (chunk_id,),
                )

        conn.execute(
            "DELETE FROM chunks WHERE dataset_version_id = ?",
            (dataset_version_id,),
        )
        conn.execute(
            "DELETE FROM documents WHERE dataset_version_id = ?",
            (dataset_version_id,),
        )

        # Insert canonical and discarded records first so duplicate_of foreign
        # keys always reference an existing canonical record.
        ordered_documents = sorted(
            clean_result.documents,
            key=lambda item: 1 if item.status == "duplicate" else 0,
        )
        for document in ordered_documents:
            conn.execute(
                '''
                INSERT INTO documents (
                    id, intake_id, dataset_version_id, crawl_page_id,
                    source_url, canonical_url, title, language,
                    meta_description, status, duplicate_of,
                    duplicate_reason, raw_path, clean_path,
                    content_sha256, normalized_sha256, word_count,
                    cleaned_text, markdown, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    document.id,
                    intake_id,
                    dataset_version_id,
                    document.crawl_page_id,
                    document.source_url,
                    document.canonical_url,
                    document.title,
                    document.language,
                    document.meta_description,
                    document.status,
                    document.duplicate_of,
                    document.duplicate_reason,
                    document.raw_path,
                    document.clean_path,
                    document.content_sha256,
                    document.normalized_sha256,
                    document.word_count,
                    document.cleaned_text,
                    document.markdown,
                    now,
                    now,
                ),
            )

        for chunk in chunks:
            conn.execute(
                '''
                INSERT INTO chunks (
                    id, intake_id, dataset_version_id, document_id,
                    ordinal, title, heading, source_url, text,
                    text_sha256, file_path, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    chunk.id,
                    intake_id,
                    dataset_version_id,
                    chunk.document_id,
                    chunk.ordinal,
                    chunk.title,
                    chunk.heading,
                    chunk.source_url,
                    chunk.text,
                    chunk.text_sha256,
                    chunk.file_path,
                    now,
                ),
            )
            if fts5_enabled:
                conn.execute(
                    '''
                    INSERT INTO chunks_fts (
                        chunk_id, intake_id, document_id, title,
                        heading, body, source_url
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        chunk.id,
                        intake_id,
                        chunk.document_id,
                        chunk.title,
                        chunk.heading or "",
                        chunk.text,
                        chunk.source_url,
                    ),
                )

    manifest = {
        "format": "nerdo-dataset-v1",
        "intake_id": intake_id,
        "dataset_version_id": dataset_version_id,
        "created_at": now,
        "crawl": {
            "crawl_run_id": crawl_result.crawl_run_id,
            "manifest": _relative_or_absolute(
                crawl_result.manifest_path,
                dataset_dir,
            ),
            "attempts": crawl_result.attempts,
            "accepted_pages": len(crawl_result.pages),
            "skipped_pages": crawl_result.skipped_pages,
            "total_bytes": crawl_result.total_bytes,
            "stop_reason": crawl_result.stop_reason,
        },
        "cleaning": {
            "report": _relative_or_absolute(
                clean_result.report_path,
                dataset_dir,
            ),
            "duplicates_report": _relative_or_absolute(
                clean_result.duplicates_path,
                dataset_dir,
            ),
            "canonical_documents": len(clean_result.canonical_documents),
            "duplicates": clean_result.duplicate_count,
            "discarded": clean_result.discarded_count,
        },
        "index": {
            "database": "sqlite",
            "fts5_enabled": fts5_enabled,
            "documents_jsonl": str(documents_path.relative_to(dataset_dir)),
            "chunks_jsonl": str(chunks_path.relative_to(dataset_dir)),
            "chunks": len(chunks),
            "chunk_chars": settings.index_chunk_chars,
            "chunk_overlap": settings.index_chunk_overlap,
        },
        "directories": {
            "raw": "raw/",
            "cleaned": "cleaned/",
            "index": "index/",
            "reports": "reports/",
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return IndexResult(
        document_count=len(clean_result.canonical_documents),
        duplicate_count=clean_result.duplicate_count,
        discarded_count=clean_result.discarded_count,
        chunk_count=len(chunks),
        fts5_enabled=fts5_enabled,
        documents_path=str(documents_path),
        chunks_path=str(chunks_path),
        manifest_path=str(manifest_path),
    )
