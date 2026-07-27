from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import posixpath
import random
import re
import socket
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import (
    parse_qsl,
    urlencode,
    urldefrag,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.robotparser import RobotFileParser
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.db import connection, execute, utc_now


LOGGER = logging.getLogger("nerdo.crawler")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
HTML_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
}
REDIRECT_CODES = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class CrawledPage:
    id: str
    requested_url: str
    final_url: str
    canonical_hint: str | None
    parent_url: str | None
    depth: int
    title: str
    language: str | None
    meta_description: str | None
    raw_path: str
    status_code: int
    content_type: str
    bytes_read: int
    content_sha256: str
    noindex: bool
    nofollow: bool


@dataclass(frozen=True)
class CrawlResult:
    crawl_run_id: str
    pages: list[CrawledPage]
    manifest_path: str
    attempts: int
    total_bytes: int
    skipped_pages: int
    stop_reason: str


@dataclass(frozen=True)
class _Fetched:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    request_count: int
    error: str | None = None
    too_large: bool = False


@dataclass(frozen=True)
class _QueuedURL:
    url: str
    depth: int
    parent_url: str | None


def normalize_url(value: str) -> str:
    clean, _ = urldefrag(value.strip())
    parts = urlsplit(clean)
    scheme = parts.scheme.casefold()
    host = (parts.hostname or "").casefold().rstrip(".")
    if not scheme or not host:
        return clean

    port = parts.port
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    else:
        netloc = host

    path = parts.path or "/"
    normalized_path = posixpath.normpath(path)
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if path.endswith("/") and not normalized_path.endswith("/"):
        normalized_path += "/"

    query_items = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit((scheme, netloc, normalized_path, query, ""))


def _site_host(host: str) -> str:
    normalized = host.casefold().rstrip(".")
    return normalized[4:] if normalized.startswith("www.") else normalized


def _same_site(candidate_host: str, start_host: str) -> bool:
    candidate = candidate_host.casefold().rstrip(".")
    start = start_host.casefold().rstrip(".")
    if _site_host(candidate) == _site_host(start):
        return True
    if settings.crawl_allow_subdomains:
        return candidate.endswith("." + start)
    return False


def _validate_public_url(value: str, start_host: str | None = None) -> str:
    normalized = normalize_url(value)
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"}:
        raise RuntimeError("Only http and https URLs are crawlable.")
    if not parts.hostname:
        raise RuntimeError("URL has no hostname.")
    if parts.username or parts.password:
        raise RuntimeError("URLs containing credentials are not crawlable.")
    if parts.port not in {None, 80, 443}:
        raise RuntimeError("Only standard HTTP and HTTPS ports are crawlable.")
    if start_host and not _same_site(parts.hostname, start_host):
        raise RuntimeError("URL is outside the submitted website.")

    try:
        addresses = socket.getaddrinfo(
            parts.hostname,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RuntimeError(f"Hostname could not be resolved: {parts.hostname}") from exc

    if not addresses:
        raise RuntimeError(f"Hostname could not be resolved: {parts.hostname}")

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise RuntimeError(
                f"Refusing non-public address for {parts.hostname}: {ip}"
            )

    return normalized


def _bounded_fetch(
    client: httpx.Client,
    url: str,
    *,
    start_host: str,
    max_bytes: int,
) -> _Fetched:
    requested_url = url
    current_url = url
    request_count = 0

    for _ in range(settings.crawl_max_redirects + 1):
        current_url = _validate_public_url(current_url, start_host)
        request_count += 1

        try:
            with client.stream("GET", current_url) as response:
                headers = {
                    key.casefold(): value
                    for key, value in response.headers.items()
                }

                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        return _Fetched(
                            requested_url=requested_url,
                            final_url=current_url,
                            status_code=response.status_code,
                            headers=headers,
                            body=b"",
                            request_count=request_count,
                            error="redirect-without-location",
                        )
                    current_url = normalize_url(urljoin(current_url, location))
                    continue

                declared_length = response.headers.get("content-length")
                if declared_length:
                    try:
                        if int(declared_length) > max_bytes:
                            return _Fetched(
                                requested_url=requested_url,
                                final_url=current_url,
                                status_code=response.status_code,
                                headers=headers,
                                body=b"",
                                request_count=request_count,
                                too_large=True,
                            )
                    except ValueError:
                        pass

                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        return _Fetched(
                            requested_url=requested_url,
                            final_url=current_url,
                            status_code=response.status_code,
                            headers=headers,
                            body=b"",
                            request_count=request_count,
                            too_large=True,
                        )

                return _Fetched(
                    requested_url=requested_url,
                    final_url=normalize_url(str(response.url)),
                    status_code=response.status_code,
                    headers=headers,
                    body=bytes(content),
                    request_count=request_count,
                )
        except httpx.HTTPError as exc:
            return _Fetched(
                requested_url=requested_url,
                final_url=current_url,
                status_code=0,
                headers={},
                body=b"",
                request_count=request_count,
                error=f"{type(exc).__name__}: {exc}",
            )

    return _Fetched(
        requested_url=requested_url,
        final_url=current_url,
        status_code=0,
        headers={},
        body=b"",
        request_count=request_count,
        error="redirect-limit-exceeded",
    )


def _robots_parser(
    client: httpx.Client,
    start_url: str,
    start_host: str,
) -> tuple[RobotFileParser, str, int, float]:
    parts = urlsplit(start_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    fetched = _bounded_fetch(
        client,
        robots_url,
        start_host=start_host,
        max_bytes=min(settings.crawl_max_page_bytes, 512_000),
    )

    parser = RobotFileParser()
    parser.set_url(robots_url)

    if fetched.error:
        raise RuntimeError(f"robots.txt could not be read: {fetched.error}")
    if fetched.too_large:
        raise RuntimeError("robots.txt exceeds the configured size limit.")

    if fetched.status_code in {404, 410}:
        parser.parse([])
    elif fetched.status_code in {401, 403}:
        parser.parse(["User-agent: *", "Disallow: /"])
    elif 200 <= fetched.status_code < 300:
        parser.parse(fetched.body.decode("utf-8", errors="replace").splitlines())
    else:
        raise RuntimeError(
            f"robots.txt returned HTTP {fetched.status_code}; crawl stopped."
        )

    robot_delay = parser.crawl_delay(settings.crawl_user_agent) or 0.0
    request_rate = parser.request_rate(settings.crawl_user_agent)
    if request_rate and request_rate.requests > 0:
        robot_delay = max(
            float(robot_delay),
            float(request_rate.seconds) / float(request_rate.requests),
        )

    return parser, fetched.final_url, fetched.status_code, float(robot_delay)


def _decode_html(body: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    encodings = []
    if charset_match:
        encodings.append(charset_match.group(1).strip('"\''))
    encodings.extend(["utf-8", "windows-1252"])

    for encoding in encodings:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def _robot_directives(
    soup: BeautifulSoup,
    headers: dict[str, str],
) -> set[str]:
    values: list[str] = []
    for tag in soup.find_all("meta"):
        name = str(tag.get("name", "")).casefold()
        if name in {"robots", "googlebot", "bingbot"}:
            values.append(str(tag.get("content", "")))
    values.append(headers.get("x-robots-tag", ""))
    return {
        item.strip().casefold()
        for value in values
        for item in re.split(r"[,\s]+", value)
        if item.strip()
    }


def _page_metadata(
    soup: BeautifulSoup,
    final_url: str,
) -> tuple[str, str | None, str | None, str | None]:
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else final_url
    )
    title = re.sub(r"\s+", " ", title).strip()[:500]

    language = None
    if soup.html and soup.html.get("lang"):
        language = str(soup.html.get("lang")).strip()[:32] or None

    description = None
    description_tag = soup.find(
        "meta",
        attrs={"name": re.compile(r"^description$", re.I)},
    )
    if description_tag and description_tag.get("content"):
        description = re.sub(
            r"\s+",
            " ",
            str(description_tag.get("content")),
        ).strip()[:2000] or None

    canonical_hint = None
    canonical_tag = soup.find(
        "link",
        attrs={"rel": lambda value: value and "canonical" in [
            str(item).casefold()
            for item in (value if isinstance(value, list) else [value])
        ]},
    )
    if canonical_tag and canonical_tag.get("href"):
        candidate = normalize_url(
            urljoin(final_url, str(canonical_tag.get("href")))
        )
        parts = urlsplit(candidate)
        if parts.hostname and _same_site(
            parts.hostname,
            urlsplit(final_url).hostname or "",
        ):
            canonical_hint = candidate

    return title, language, description, canonical_hint


def _insert_crawl_page(
    *,
    page_id: str,
    crawl_run_id: str,
    intake_id: str,
    requested_url: str,
    final_url: str | None,
    canonical_hint: str | None,
    parent_url: str | None,
    depth: int,
    status_code: int | None,
    content_type: str | None,
    bytes_read: int,
    content_sha256: str | None,
    raw_path: str | None,
    title: str | None,
    language: str | None,
    meta_description: str | None,
    noindex: bool,
    nofollow: bool,
    outcome: str,
    skip_reason: str | None,
    delay_seconds: float,
) -> None:
    execute(
        '''
        INSERT INTO crawl_pages (
            id, crawl_run_id, intake_id, requested_url, final_url,
            canonical_hint, parent_url, depth, status_code, content_type,
            bytes_read, content_sha256, raw_path, title, language,
            meta_description, noindex, nofollow, outcome, skip_reason,
            delay_seconds, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            page_id,
            crawl_run_id,
            intake_id,
            requested_url,
            final_url,
            canonical_hint,
            parent_url,
            depth,
            status_code,
            content_type,
            bytes_read,
            content_sha256,
            raw_path,
            title,
            language,
            meta_description,
            1 if noindex else 0,
            1 if nofollow else 0,
            outcome,
            skip_reason,
            round(delay_seconds, 3),
            utc_now(),
        ),
    )


def crawl(
    start_url: str,
    intake_id: str,
    dataset_version_id: str,
    raw_dir: Path,
) -> CrawlResult:
    raw_pages_dir = raw_dir / "pages"
    raw_pages_dir.mkdir(parents=True, exist_ok=True)

    start_url = _validate_public_url(start_url)
    start_parts = urlsplit(start_url)
    start_host = start_parts.hostname or ""
    crawl_run_id = str(uuid4())
    started_at = utc_now()

    execute(
        '''
        INSERT INTO crawl_runs (
            id, intake_id, dataset_version_id, start_url, status, started_at
        )
        VALUES (?, ?, ?, ?, 'running', ?)
        ''',
        (crawl_run_id, intake_id, dataset_version_id, start_url, started_at),
    )

    headers = {
        "User-Agent": settings.crawl_user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Language": "en,es;q=0.8,*;q=0.5",
    }
    queue: deque[_QueuedURL] = deque([_QueuedURL(start_url, 0, None)])
    queued = {start_url}
    visited: set[str] = set()
    accepted: list[CrawledPage] = []
    manifest_records: list[dict[str, Any]] = []
    attempts = 0
    skipped = 0
    total_bytes = 0
    start_clock = time.monotonic()
    last_request_at: float | None = None
    stop_reason = "frontier-exhausted"

    try:
        with httpx.Client(
            headers=headers,
            follow_redirects=False,
            timeout=settings.crawl_timeout_seconds,
        ) as client:
            robots, robots_url, robots_status, robot_delay = _robots_parser(
                client,
                start_url,
                start_host,
            )
            execute(
                '''
                UPDATE crawl_runs
                SET robots_url = ?, robots_status = ?
                WHERE id = ?
                ''',
                (robots_url, robots_status, crawl_run_id),
            )
            # robots.txt is an HTTP hit; wait before the first page request.
            last_request_at = time.monotonic()

            while queue:
                elapsed = time.monotonic() - start_clock
                if len(accepted) >= settings.crawl_max_pages:
                    stop_reason = "page-limit"
                    break
                if attempts >= settings.crawl_max_attempts:
                    stop_reason = "attempt-limit"
                    break
                if elapsed >= settings.crawl_max_duration_seconds:
                    stop_reason = "duration-limit"
                    break
                if total_bytes >= settings.crawl_max_total_bytes:
                    stop_reason = "byte-limit"
                    break

                item = queue.popleft()
                if item.url in visited:
                    continue
                visited.add(item.url)
                attempts += 1
                page_id = str(uuid4())

                if not robots.can_fetch(settings.crawl_user_agent, item.url):
                    skipped += 1
                    _insert_crawl_page(
                        page_id=page_id,
                        crawl_run_id=crawl_run_id,
                        intake_id=intake_id,
                        requested_url=item.url,
                        final_url=None,
                        canonical_hint=None,
                        parent_url=item.parent_url,
                        depth=item.depth,
                        status_code=None,
                        content_type=None,
                        bytes_read=0,
                        content_sha256=None,
                        raw_path=None,
                        title=None,
                        language=None,
                        meta_description=None,
                        noindex=False,
                        nofollow=False,
                        outcome="skipped",
                        skip_reason="robots-disallowed",
                        delay_seconds=0.0,
                    )
                    manifest_records.append(
                        {
                            "id": page_id,
                            "requested_url": item.url,
                            "depth": item.depth,
                            "outcome": "skipped",
                            "skip_reason": "robots-disallowed",
                        }
                    )
                    continue

                delay_low = max(
                    settings.crawl_min_delay_seconds,
                    robot_delay,
                )
                delay_high = max(
                    settings.crawl_max_delay_seconds,
                    delay_low,
                )
                planned_delay = random.SystemRandom().uniform(
                    delay_low,
                    delay_high,
                )
                if last_request_at is not None:
                    remaining = planned_delay - (
                        time.monotonic() - last_request_at
                    )
                    if remaining > 0:
                        time.sleep(remaining)

                remaining_bytes = max(
                    0,
                    settings.crawl_max_total_bytes - total_bytes,
                )
                page_limit = min(
                    settings.crawl_max_page_bytes,
                    remaining_bytes,
                )
                if page_limit <= 0:
                    stop_reason = "byte-limit"
                    break

                last_request_at = time.monotonic()
                fetched = _bounded_fetch(
                    client,
                    item.url,
                    start_host=start_host,
                    max_bytes=page_limit,
                )
                content_type_header = fetched.headers.get("content-type", "")
                media_type = content_type_header.split(";", 1)[0].strip().casefold()
                bytes_read = len(fetched.body)
                total_bytes += bytes_read
                outcome = "fetched"
                skip_reason = None
                raw_path: str | None = None
                title: str | None = None
                language: str | None = None
                description: str | None = None
                canonical_hint: str | None = None
                noindex = False
                nofollow = False
                content_sha256 = (
                    hashlib.sha256(fetched.body).hexdigest()
                    if fetched.body
                    else None
                )

                if fetched.error:
                    outcome = "failed"
                    skip_reason = fetched.error
                elif fetched.too_large:
                    outcome = "skipped"
                    skip_reason = "page-byte-limit"
                elif fetched.status_code != 200:
                    outcome = "skipped"
                    skip_reason = f"http-{fetched.status_code}"
                elif media_type not in HTML_CONTENT_TYPES:
                    outcome = "skipped"
                    skip_reason = f"unsupported-content-type:{media_type or 'unknown'}"
                else:
                    html = _decode_html(fetched.body, content_type_header)
                    soup = BeautifulSoup(html, "html.parser")
                    directives = _robot_directives(soup, fetched.headers)
                    noindex = "noindex" in directives or "none" in directives
                    nofollow = "nofollow" in directives or "none" in directives
                    title, language, description, canonical_hint = _page_metadata(
                        soup,
                        fetched.final_url,
                    )
                    raw_file = raw_pages_dir / f"{page_id}.html"
                    raw_file.write_bytes(fetched.body)
                    raw_path = str(raw_file)

                    if noindex:
                        outcome = "fetched-noindex"
                        skip_reason = "page-noindex"
                    else:
                        accepted.append(
                            CrawledPage(
                                id=page_id,
                                requested_url=item.url,
                                final_url=fetched.final_url,
                                canonical_hint=canonical_hint,
                                parent_url=item.parent_url,
                                depth=item.depth,
                                title=title,
                                language=language,
                                meta_description=description,
                                raw_path=raw_path,
                                status_code=fetched.status_code,
                                content_type=content_type_header,
                                bytes_read=bytes_read,
                                content_sha256=content_sha256 or "",
                                noindex=noindex,
                                nofollow=nofollow,
                            )
                        )

                    if not nofollow and item.depth < settings.crawl_max_depth:
                        discovered: list[str] = []
                        for anchor in soup.find_all("a", href=True):
                            rel = {
                                str(value).casefold()
                                for value in (anchor.get("rel") or [])
                            }
                            if "nofollow" in rel:
                                continue
                            candidate = normalize_url(
                                urljoin(
                                    fetched.final_url,
                                    str(anchor.get("href", "")),
                                )
                            )
                            parts = urlsplit(candidate)
                            if parts.scheme not in {"http", "https"}:
                                continue
                            if not parts.hostname or not _same_site(
                                parts.hostname,
                                start_host,
                            ):
                                continue
                            if candidate in queued or candidate in visited:
                                continue
                            discovered.append(candidate)

                        # A deterministic set, then a random order, avoids repeated
                        # menu duplicates while preventing a fixed hit pattern.
                        unique_links = sorted(set(discovered))
                        random.SystemRandom().shuffle(unique_links)
                        for candidate in unique_links[
                            : settings.crawl_max_links_per_page
                        ]:
                            queued.add(candidate)
                            queue.append(
                                _QueuedURL(
                                    candidate,
                                    item.depth + 1,
                                    fetched.final_url,
                                )
                            )

                if outcome != "fetched":
                    skipped += 1

                _insert_crawl_page(
                    page_id=page_id,
                    crawl_run_id=crawl_run_id,
                    intake_id=intake_id,
                    requested_url=item.url,
                    final_url=fetched.final_url,
                    canonical_hint=canonical_hint,
                    parent_url=item.parent_url,
                    depth=item.depth,
                    status_code=fetched.status_code,
                    content_type=content_type_header or None,
                    bytes_read=bytes_read,
                    content_sha256=content_sha256,
                    raw_path=raw_path,
                    title=title,
                    language=language,
                    meta_description=description,
                    noindex=noindex,
                    nofollow=nofollow,
                    outcome=outcome,
                    skip_reason=skip_reason,
                    delay_seconds=planned_delay,
                )
                manifest_records.append(
                    {
                        "id": page_id,
                        "requested_url": item.url,
                        "final_url": fetched.final_url,
                        "canonical_hint": canonical_hint,
                        "parent_url": item.parent_url,
                        "depth": item.depth,
                        "status_code": fetched.status_code,
                        "content_type": content_type_header,
                        "bytes_read": bytes_read,
                        "content_sha256": content_sha256,
                        "raw_path": raw_path,
                        "title": title,
                        "language": language,
                        "meta_description": description,
                        "noindex": noindex,
                        "nofollow": nofollow,
                        "outcome": outcome,
                        "skip_reason": skip_reason,
                        "delay_seconds": round(planned_delay, 3),
                    }
                )

        manifest_path = raw_dir / "crawl-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "crawl_run_id": crawl_run_id,
                    "start_url": start_url,
                    "user_agent": settings.crawl_user_agent,
                    "limits": {
                        "max_pages": settings.crawl_max_pages,
                        "max_attempts": settings.crawl_max_attempts,
                        "max_depth": settings.crawl_max_depth,
                        "max_duration_seconds": settings.crawl_max_duration_seconds,
                        "max_page_bytes": settings.crawl_max_page_bytes,
                        "max_total_bytes": settings.crawl_max_total_bytes,
                        "delay_range_seconds": [
                            settings.crawl_min_delay_seconds,
                            settings.crawl_max_delay_seconds,
                        ],
                    },
                    "stop_reason": stop_reason,
                    "attempts": attempts,
                    "accepted_pages": len(accepted),
                    "skipped_pages": skipped,
                    "total_bytes": total_bytes,
                    "pages": manifest_records,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        execute(
            '''
            UPDATE crawl_runs
            SET status = 'done', attempts = ?, fetched_pages = ?,
                accepted_pages = ?, skipped_pages = ?, total_bytes = ?,
                stop_reason = ?, finished_at = ?
            WHERE id = ?
            ''',
            (
                attempts,
                len(manifest_records),
                len(accepted),
                skipped,
                total_bytes,
                stop_reason,
                utc_now(),
                crawl_run_id,
            ),
        )

        return CrawlResult(
            crawl_run_id=crawl_run_id,
            pages=accepted,
            manifest_path=str(manifest_path),
            attempts=attempts,
            total_bytes=total_bytes,
            skipped_pages=skipped,
            stop_reason=stop_reason,
        )
    except Exception as exc:
        execute(
            '''
            UPDATE crawl_runs
            SET status = 'failed', attempts = ?, fetched_pages = ?,
                accepted_pages = ?, skipped_pages = ?, total_bytes = ?,
                stop_reason = ?, error = ?, finished_at = ?
            WHERE id = ?
            ''',
            (
                attempts,
                len(manifest_records),
                len(accepted),
                skipped,
                total_bytes,
                stop_reason,
                str(exc)[:4000],
                utc_now(),
                crawl_run_id,
            ),
        )
        raise
