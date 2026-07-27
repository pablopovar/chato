from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup, Comment, Tag

from app.config import settings
from app.services.crawler import CrawledPage, normalize_url


BLOCK_TAGS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "blockquote",
    "dt",
    "dd",
}
REMOVE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "template",
    "canvas",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "textarea",
    "select",
    "button",
    "nav",
    "header",
    "footer",
    "aside",
    "dialog",
    "menu",
    "code",
    "pre",
}
NOISE_PATTERN = re.compile(
    r"(?:^|[-_\s])(?:cookie|consent|modal|popup|breadcrumb|sidebar|"
    r"social|share|advert|newsletter|subscribe|pagination|toolbar|"
    r"skip-link|screen-reader)(?:$|[-_\s])",
    re.I,
)
WHITESPACE_PATTERN = re.compile(r"\s+")
WORD_PATTERN = re.compile(r"[\wáéíóúüñç]+", re.I)


@dataclass(frozen=True)
class CleanedDocument:
    id: str
    crawl_page_id: str
    source_url: str
    canonical_url: str
    title: str
    language: str | None
    meta_description: str | None
    status: str
    duplicate_of: str | None
    duplicate_reason: str | None
    raw_path: str
    clean_path: str | None
    content_sha256: str
    normalized_sha256: str
    word_count: int
    cleaned_text: str
    markdown: str


@dataclass(frozen=True)
class CleanResult:
    documents: list[CleanedDocument]
    canonical_documents: list[CleanedDocument]
    duplicate_count: int
    discarded_count: int
    report_path: str
    duplicates_path: str


@dataclass
class _Candidate:
    document: CleanedDocument
    blocks: list[tuple[str, int | None]]
    normalized_text: str
    shingles: set[str]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _clean_space(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value).strip()


def _normalize_content(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^\wáéíóúüñç]+", " ", value, flags=re.I)
    return _clean_space(value)




def _duplicate_normalized(
    blocks: list[tuple[str, int | None]],
    title: str,
) -> str:
    body = "\n\n".join(
        text
        for text, level in blocks
        if level is None
    ).strip()
    if not body:
        body = "\n\n".join(text for text, _ in blocks).strip()
    normalized = _normalize_content(body)
    if normalized:
        return normalized
    return _normalize_content(title)


def _shingles(value: str, width: int = 5) -> set[str]:
    words = WORD_PATTERN.findall(value.casefold())
    if len(words) < width:
        return {" ".join(words)} if words else set()
    return {
        " ".join(words[index : index + width])
        for index in range(len(words) - width + 1)
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _remove_noise(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    for element in list(soup.find_all(True)):
        if element.name is None or element.attrs is None:
            continue
        if element.name in REMOVE_TAGS:
            element.decompose()
            continue

        if element.has_attr("hidden"):
            element.decompose()
            continue
        if str(element.get("aria-hidden", "")).casefold() == "true":
            element.decompose()
            continue

        style = str(element.get("style", "")).casefold().replace(" ", "")
        if "display:none" in style or "visibility:hidden" in style:
            element.decompose()
            continue

        identifiers = " ".join(
            [
                str(element.get("id", "")),
                " ".join(str(value) for value in element.get("class", [])),
                str(element.get("role", "")),
            ]
        )
        if identifiers and NOISE_PATTERN.search(identifiers):
            element.decompose()


def _content_root(soup: BeautifulSoup) -> Tag:
    for selector in ("main", "article", '[role="main"]'):
        root = soup.select_one(selector)
        if isinstance(root, Tag):
            return root
    if soup.body:
        return soup.body
    return soup


def _block_text(tag: Tag) -> str:
    return _clean_space(tag.get_text(" ", strip=True))


def _extract_blocks(root: Tag) -> list[tuple[str, int | None]]:
    blocks: list[tuple[str, int | None]] = []
    previous_normalized = ""

    for tag in root.find_all([*BLOCK_TAGS, "tr"]):
        if tag.name == "p" and tag.find_parent("li"):
            continue
        if tag.name in {"li", "blockquote", "dt", "dd"} and tag.find_parent(
            ["li", "blockquote", "dt", "dd"]
        ):
            continue

        if tag.name == "tr":
            cells = [
                _clean_space(cell.get_text(" ", strip=True))
                for cell in tag.find_all(["th", "td"], recursive=False)
            ]
            text = " | ".join(cell for cell in cells if cell)
        else:
            text = _block_text(tag)

        if not text:
            continue

        normalized = _normalize_content(text)
        if not normalized or normalized == previous_normalized:
            continue
        previous_normalized = normalized

        level = None
        if tag.name and tag.name.startswith("h") and tag.name[1:].isdigit():
            level = min(6, max(2, int(tag.name[1:]) + 1))
        blocks.append((text, level))

    if not blocks:
        text = _clean_space(root.get_text(" ", strip=True))
        if text:
            blocks.append((text, None))

    return blocks


def _candidate(page: CrawledPage) -> _Candidate:
    raw = Path(page.raw_path).read_bytes()
    html = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    _remove_noise(soup)
    root = _content_root(soup)
    blocks = _extract_blocks(root)
    text = "\n\n".join(block for block, _ in blocks).strip()
    normalized = _duplicate_normalized(blocks, page.title)
    document_id = str(uuid4())
    canonical_url = normalize_url(page.canonical_hint or page.final_url)

    document = CleanedDocument(
        id=document_id,
        crawl_page_id=page.id,
        source_url=page.final_url,
        canonical_url=canonical_url,
        title=_clean_space(page.title) or page.final_url,
        language=page.language,
        meta_description=page.meta_description,
        status="candidate",
        duplicate_of=None,
        duplicate_reason=None,
        raw_path=page.raw_path,
        clean_path=None,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        normalized_sha256=hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest(),
        word_count=len(WORD_PATTERN.findall(text)),
        cleaned_text=text,
        markdown="",
    )
    return _Candidate(
        document=document,
        blocks=blocks,
        normalized_text=normalized,
        shingles=_shingles(normalized),
    )


def _remove_common_boilerplate(candidates: list[_Candidate]) -> None:
    if len(candidates) < 3:
        return

    frequency: Counter[str] = Counter()
    for candidate in candidates:
        seen = {
            _normalize_content(text)
            for text, level in candidate.blocks
            if level is None and 2 <= len(text.split()) <= 24 and len(text) <= 180
        }
        frequency.update(item for item in seen if item)

    threshold = max(3, math.ceil(len(candidates) * 0.60))
    common = {
        value
        for value, count in frequency.items()
        if count >= threshold
    }
    if not common:
        return

    for candidate in candidates:
        filtered = [
            (text, level)
            for text, level in candidate.blocks
            if level is not None or _normalize_content(text) not in common
        ]
        if not filtered:
            continue
        candidate.blocks = filtered
        text = "\n\n".join(block for block, _ in filtered).strip()
        normalized = _duplicate_normalized(filtered, candidate.document.title)
        candidate.normalized_text = normalized
        candidate.shingles = _shingles(normalized)
        candidate.document = replace(
            candidate.document,
            cleaned_text=text,
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            normalized_sha256=hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest(),
            word_count=len(WORD_PATTERN.findall(text)),
        )


def _duplicate_reason(left: _Candidate, right: _Candidate) -> str | None:
    if left.document.normalized_sha256 == right.document.normalized_sha256:
        return "exact-content"
    if left.document.canonical_url == right.document.canonical_url:
        return "canonical-url"
    if min(left.document.word_count, right.document.word_count) < 40:
        return None
    similarity = _jaccard(left.shingles, right.shingles)
    if similarity >= settings.clean_near_duplicate_threshold:
        return f"near-content:{similarity:.3f}"
    return None


def _canonical_rank(candidate: _Candidate) -> tuple[int, int, int, int]:
    source = urlsplit(candidate.document.source_url)
    canonical = normalize_url(candidate.document.canonical_url)
    source_url = normalize_url(candidate.document.source_url)
    return (
        1 if canonical == source_url else 0,
        1 if not source.query else 0,
        candidate.document.word_count,
        -len(source_url),
    )


def _safe_filename(document: CleanedDocument) -> str:
    parts = urlsplit(document.canonical_url)
    stem = re.sub(
        r"[^a-z0-9]+",
        "-",
        (parts.path.strip("/") or "home").casefold(),
    ).strip("-")
    stem = stem[-80:] or "page"
    digest = hashlib.sha256(
        document.canonical_url.encode("utf-8")
    ).hexdigest()[:12]
    return f"{stem}-{digest}.md"


def _markdown(candidate: _Candidate, document: CleanedDocument) -> str:
    frontmatter = [
        "---",
        f"document_id: {json.dumps(document.id)}",
        f"source_url: {json.dumps(document.source_url)}",
        f"canonical_url: {json.dumps(document.canonical_url)}",
        f"title: {json.dumps(document.title, ensure_ascii=False)}",
        f"language: {json.dumps(document.language)}",
        f"content_sha256: {json.dumps(document.content_sha256)}",
        f"word_count: {document.word_count}",
        "---",
        "",
        f"# {document.title}",
        "",
    ]
    body: list[str] = []
    for text, level in candidate.blocks:
        if level is not None:
            if _normalize_content(text) == _normalize_content(document.title):
                continue
            body.extend(["#" * level + " " + text, ""])
        else:
            body.extend([text, ""])
    return "\n".join(frontmatter + body).rstrip() + "\n"


def clean_pages(
    pages: list[CrawledPage],
    cleaned_dir: Path,
) -> CleanResult:
    cleaned_pages_dir = cleaned_dir / "pages"
    cleaned_pages_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = cleaned_dir.parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    candidates = [_candidate(page) for page in pages]
    _remove_common_boilerplate(candidates)

    viable_indices = [
        index
        for index, candidate in enumerate(candidates)
        if len(candidate.document.cleaned_text) >= settings.clean_min_document_chars
    ]
    union_find = _UnionFind(len(candidates))
    pair_reasons: dict[tuple[int, int], str] = {}

    for position, left_index in enumerate(viable_indices):
        for right_index in viable_indices[position + 1 :]:
            reason = _duplicate_reason(
                candidates[left_index],
                candidates[right_index],
            )
            if reason:
                union_find.union(left_index, right_index)
                pair_reasons[(left_index, right_index)] = reason

    clusters: dict[int, list[int]] = {}
    for index in viable_indices:
        clusters.setdefault(union_find.find(index), []).append(index)

    canonical_by_index: dict[int, int] = {}
    for members in clusters.values():
        canonical_index = max(
            members,
            key=lambda index: _canonical_rank(candidates[index]),
        )
        for member in members:
            canonical_by_index[member] = canonical_index

    documents: list[CleanedDocument] = []
    duplicate_records: list[dict[str, str]] = []

    for index, candidate in enumerate(candidates):
        document = candidate.document
        if index not in viable_indices:
            document = replace(
                document,
                status="discarded",
                duplicate_reason="insufficient-readable-content",
                markdown="",
            )
            documents.append(document)
            continue

        canonical_index = canonical_by_index[index]
        if canonical_index != index:
            canonical_document = candidates[canonical_index].document
            reason = _duplicate_reason(
                candidate,
                candidates[canonical_index],
            ) or "duplicate-cluster"
            document = replace(
                document,
                status="duplicate",
                duplicate_of=canonical_document.id,
                duplicate_reason=reason,
                markdown="",
            )
            duplicate_records.append(
                {
                    "document_id": document.id,
                    "source_url": document.source_url,
                    "duplicate_of": canonical_document.id,
                    "canonical_source_url": canonical_document.source_url,
                    "reason": reason,
                }
            )
            documents.append(document)
            continue

        markdown = _markdown(candidate, document)
        clean_path = cleaned_pages_dir / _safe_filename(document)
        clean_path.write_text(markdown, encoding="utf-8")
        document = replace(
            document,
            status="canonical",
            clean_path=str(clean_path),
            markdown=markdown,
        )
        candidates[index].document = document
        documents.append(document)

    canonical_documents = [
        document
        for document in documents
        if document.status == "canonical"
    ]
    duplicates_path = reports_dir / "duplicates.json"
    duplicates_path.write_text(
        json.dumps(duplicate_records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    dataset_dir = cleaned_dir.parent
    report_documents = []
    for document in documents:
        record = {
            key: value
            for key, value in asdict(document).items()
            if key not in {"cleaned_text", "markdown"}
        }
        for path_key in ("raw_path", "clean_path"):
            if record.get(path_key):
                try:
                    record[path_key] = str(
                        Path(record[path_key]).relative_to(dataset_dir)
                    )
                except ValueError:
                    pass
        report_documents.append(record)

    report = {
        "input_pages": len(pages),
        "canonical_documents": len(canonical_documents),
        "duplicates": sum(
            1 for document in documents if document.status == "duplicate"
        ),
        "discarded": sum(
            1 for document in documents if document.status == "discarded"
        ),
        "minimum_document_chars": settings.clean_min_document_chars,
        "near_duplicate_threshold": settings.clean_near_duplicate_threshold,
        "documents": report_documents,
    }
    report_path = reports_dir / "cleaning.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return CleanResult(
        documents=documents,
        canonical_documents=canonical_documents,
        duplicate_count=report["duplicates"],
        discarded_count=report["discarded"],
        report_path=str(report_path),
        duplicates_path=str(duplicates_path),
    )
