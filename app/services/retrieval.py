from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.services.chat_trace import current_trace
from app.services.registry import BotConfig


WORD_PATTERN = re.compile(r"[a-z0-9áéíóúüñç]+", re.IGNORECASE)
HEADING_PATTERN = re.compile(
    r"^\s{0,3}#{1,6}\s+(.+?)\s*$",
    re.MULTILINE,
)
FRONTMATTER_PATTERN = re.compile(
    r"\A---\s*\n.*?\n---\s*\n",
    re.DOTALL,
)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can",
    "como", "con", "de", "del", "do", "does", "el", "en", "es",
    "for", "from", "how", "i", "in", "is", "it", "la", "las",
    "los", "of", "on", "or", "para", "por", "que", "qué", "the",
    "to", "un", "una", "what", "where", "which", "who", "with",
    "y",
}


@dataclass(frozen=True)
class SearchHit:
    title: str
    path: str
    text: str
    score: float


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char)
    )
    return plain.casefold()


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in WORD_PATTERN.findall(_normalize(value))
        if len(token) > 1 and token not in STOPWORDS
    ]


def _plain(markdown: str) -> str:
    text = FRONTMATTER_PATTERN.sub("", markdown, count=1)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(
        r"^\s{0,3}#{1,6}\s*",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _title(markdown: str, path: Path) -> str:
    match = HEADING_PATTERN.search(markdown)
    if match:
        return match.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def _chunks(text: str, size: int = 1800) -> list[str]:
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", text)
        if item.strip()
    ]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = paragraph[:size]
    if current:
        chunks.append(current)
    return chunks


def _hit_record(hit: SearchHit, rank: int | None = None) -> dict[str, object]:
    record: dict[str, object] = {
        "title": hit.title,
        "path": hit.path,
        "score": hit.score,
        "text": hit.text,
    }
    if rank is not None:
        record["rank"] = rank
    return record


def search(
    config: BotConfig,
    question: str,
) -> list[SearchHit]:
    trace = current_trace()
    started = time.perf_counter()
    query_tokens = set(_tokens(question))
    if trace:
        trace.event(
            "retrieval.started",
            question=question,
            normalized_question=_normalize(question),
            query_tokens=sorted(query_tokens),
            corpus_directory=str(config.directory),
            maximum_results=config.max_results,
        )
    if not query_tokens:
        if trace:
            trace.event(
                "retrieval.completed",
                document_count=0,
                chunk_count=0,
                candidate_count=0,
                selected_count=0,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                reason="no-query-tokens",
                candidates=[],
                selected=[],
            )
        return []

    normalized_question = _normalize(question)
    hits: list[SearchHit] = []
    document_count = 0
    chunk_count = 0

    for path in sorted(config.directory.rglob("*.md")):
        document_count += 1
        markdown = path.read_text(encoding="utf-8", errors="replace")
        title = _title(markdown, path)
        normalized_title = _normalize(title)
        relative_path = str(path.relative_to(config.directory))

        for chunk in _chunks(_plain(markdown)):
            chunk_count += 1
            normalized_chunk = _normalize(chunk)
            matched = 0
            score = 0.0

            if normalized_question in normalized_chunk:
                score += 24.0

            for token in query_tokens:
                title_count = normalized_title.count(token)
                text_count = normalized_chunk.count(token)
                if title_count or text_count:
                    matched += 1
                score += min(title_count, 3) * 7.0
                score += min(text_count, 10) * 1.4

            coverage = matched / len(query_tokens)
            score += coverage * 20.0
            if coverage == 1.0:
                score += 8.0

            if score > 0:
                hits.append(
                    SearchHit(
                        title=title,
                        path=relative_path,
                        text=chunk,
                        score=round(score, 3),
                    )
                )

    hits.sort(key=lambda item: (-item.score, item.path))
    selected = hits[: config.max_results]
    if trace:
        trace.event(
            "retrieval.completed",
            document_count=document_count,
            chunk_count=chunk_count,
            candidate_count=len(hits),
            selected_count=len(selected),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            candidates=[
                _hit_record(hit, index)
                for index, hit in enumerate(hits, start=1)
            ],
            selected=[
                _hit_record(hit, index)
                for index, hit in enumerate(selected, start=1)
            ],
        )
    return selected
