from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.config import settings
from app.db import (
    enqueue_job,
    execute,
    fetch_one,
    token_hash,
    utc_now,
)
from app.services.cleaner import clean_pages
from app.services.crawler import crawl
from app.services.email_transport import send_email
from app.services.indexer import build_index
from app.services.interpreter import interpret
from app.services.registry import normalize_domain
from app.services.setup_report import build_setup_report


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned[:64] or "customer"


def create_intake(
    website_url: str,
    email: str,
    business_name: str | None,
) -> tuple[str, str]:
    parts = urlsplit(website_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("A valid http or https website URL is required.")

    domain = normalize_domain(parts.hostname)
    intake_id = str(uuid4())
    status_token = secrets.token_urlsafe(32)
    now = utc_now()

    execute(
        '''
        INSERT INTO intakes (
            id, status_token_hash, email, website_url, domain,
            business_name, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
        ''',
        (
            intake_id,
            token_hash(status_token),
            email,
            website_url,
            domain,
            business_name,
            now,
            now,
        ),
    )
    enqueue_job("process_intake", intake_id)
    return intake_id, status_token


def process_intake(intake_id: str) -> None:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise RuntimeError("Intake not found.")

    intake_dir = settings.data_dir / "intakes" / intake_id
    dataset_version_id = str(uuid4())
    dataset_dir = intake_dir / "datasets" / dataset_version_id
    raw_dir = dataset_dir / "raw"
    cleaned_dir = dataset_dir / "cleaned"
    summary_path = intake_dir / "chato-summary.md"
    report_path = intake_dir / "setup-report.md"
    now = utc_now()

    dataset_dir.mkdir(parents=True, exist_ok=False)
    execute(
        '''
        INSERT INTO dataset_versions (
            id, intake_id, status, dataset_path, created_at, updated_at
        )
        VALUES (?, ?, 'crawling', ?, ?, ?)
        ''',
        (
            dataset_version_id,
            intake_id,
            str(dataset_dir),
            now,
            now,
        ),
    )
    execute(
        '''
        UPDATE intakes
        SET status = 'crawling', error = NULL,
            dataset_version_id = ?, dataset_path = ?,
            draft_path = NULL, report_path = NULL,
            fetched_page_count = 0, document_count = 0,
            duplicate_count = 0, chunk_count = 0,
            updated_at = ?
        WHERE id = ?
        ''',
        (dataset_version_id, str(dataset_dir), now, intake_id),
    )

    try:
        crawl_result = crawl(
            intake["website_url"],
            intake_id,
            dataset_version_id,
            raw_dir,
        )
        if not crawl_result.pages:
            raise RuntimeError(
                "No indexable pages were collected under the configured rules."
            )

        execute(
            '''
            UPDATE intakes
            SET status = 'cleaning', fetched_page_count = ?, updated_at = ?
            WHERE id = ?
            ''',
            (len(crawl_result.pages), utc_now(), intake_id),
        )
        execute(
            '''
            UPDATE dataset_versions
            SET status = 'cleaning', crawl_run_id = ?,
                fetched_page_count = ?, updated_at = ?
            WHERE id = ?
            ''',
            (
                crawl_result.crawl_run_id,
                len(crawl_result.pages),
                utc_now(),
                dataset_version_id,
            ),
        )

        clean_result = clean_pages(crawl_result.pages, cleaned_dir)
        if not clean_result.canonical_documents:
            raise RuntimeError(
                "The collected pages did not produce a usable cleaned dataset."
            )

        execute(
            '''
            UPDATE intakes
            SET status = 'indexing', document_count = ?,
                duplicate_count = ?, updated_at = ?
            WHERE id = ?
            ''',
            (
                len(clean_result.canonical_documents),
                clean_result.duplicate_count,
                utc_now(),
                intake_id,
            ),
        )
        execute(
            '''
            UPDATE dataset_versions
            SET status = 'indexing', document_count = ?,
                duplicate_count = ?, updated_at = ?
            WHERE id = ?
            ''',
            (
                len(clean_result.canonical_documents),
                clean_result.duplicate_count,
                utc_now(),
                dataset_version_id,
            ),
        )

        index_result = build_index(
            intake_id=intake_id,
            dataset_version_id=dataset_version_id,
            dataset_dir=dataset_dir,
            crawl_result=crawl_result,
            clean_result=clean_result,
        )
        execute(
            '''
            UPDATE dataset_versions
            SET status = 'indexed', manifest_path = ?,
                document_count = ?, duplicate_count = ?, chunk_count = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (
                index_result.manifest_path,
                index_result.document_count,
                index_result.duplicate_count,
                index_result.chunk_count,
                utc_now(),
                dataset_version_id,
            ),
        )
        execute(
            '''
            UPDATE intakes
            SET status = 'interpreting', document_count = ?,
                duplicate_count = ?, chunk_count = ?, updated_at = ?
            WHERE id = ?
            ''',
            (
                index_result.document_count,
                index_result.duplicate_count,
                index_result.chunk_count,
                utc_now(),
                intake_id,
            ),
        )

        # Chato must read the canonical corpus and produce a usable summary.
        # Model failure is a setup failure; it is not replaced with a source
        # inventory that could later pollute retrieval as knowledge.md.
        interpret(
            intake["domain"],
            cleaned_dir / "pages",
            summary_path,
        )
        completed_at = utc_now()
        build_setup_report(
            domain=intake["domain"],
            website_url=intake["website_url"],
            owner_email=intake["email"],
            started_at=intake["created_at"],
            completed_at=completed_at,
            crawl_result=crawl_result,
            clean_result=clean_result,
            index_result=index_result,
            chato_summary_path=summary_path,
            report_path=report_path,
        )

        execute(
            '''
            UPDATE dataset_versions
            SET status = 'ready', updated_at = ?
            WHERE id = ?
            ''',
            (completed_at, dataset_version_id),
        )
        execute(
            '''
            UPDATE intakes
            SET status = 'awaiting_review', draft_path = ?, report_path = ?,
                updated_at = ?
            WHERE id = ?
            ''',
            (str(summary_path), str(report_path), completed_at, intake_id),
        )
    except Exception as exc:
        execute(
            '''
            UPDATE dataset_versions
            SET status = 'failed', error = ?, updated_at = ?
            WHERE id = ?
            ''',
            (str(exc)[:4000], utc_now(), dataset_version_id),
        )
        raise


def send_clarification(
    intake_id: str,
    subject: str,
    message: str,
) -> None:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise RuntimeError("Intake not found.")
    if intake["clarification_count"] >= settings.max_clarification_emails:
        raise RuntimeError("Clarification-email limit reached.")

    send_email(
        to_email=intake["email"],
        subject=subject,
        body=message,
    )
    execute(
        '''
        UPDATE intakes
        SET clarification_count = clarification_count + 1,
            status = 'awaiting_clarification',
            updated_at = ?
        WHERE id = ?
        ''',
        (utc_now(), intake_id),
    )


def activate_intake(
    intake_id: str,
    *,
    bot_name: str | None,
    system_prompt: str | None,
    allowed_origins: list[str],
    welcome_subject: str,
    welcome_message: str | None,
    test_url: str | None,
) -> dict[str, str]:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise RuntimeError("Intake not found.")
    if not intake["draft_path"]:
        raise RuntimeError("The intake has no Chato corpus summary.")
    if not intake.get("report_path"):
        raise RuntimeError("The intake has no completed setup report.")

    draft_path = Path(intake["draft_path"])
    if not draft_path.is_file():
        raise RuntimeError("The Chato corpus summary file is missing.")
    if not Path(intake["report_path"]).is_file():
        raise RuntimeError("The website setup report file is missing.")

    email_local = intake["email"].split("@", 1)[0]
    user_slug = _slug(email_local)
    domain_dir = settings.users_dir / user_slug / intake["domain"]
    domain_dir.mkdir(parents=True, exist_ok=True)

    knowledge_path = domain_dir / "knowledge.md"
    shutil.copy2(draft_path, knowledge_path)

    # Bridge the prepared dataset into the current file-based chatbot. The
    # SQLite and versioned intake dataset remain the authoritative pipeline
    # records; these are deployment copies.
    if intake.get("dataset_path"):
        source_pages = Path(intake["dataset_path"]) / "cleaned" / "pages"
        deployed_pages = domain_dir / "source-pages"
        if deployed_pages.exists():
            shutil.rmtree(deployed_pages)
        if source_pages.is_dir():
            shutil.copytree(source_pages, deployed_pages)

    key = secrets.token_urlsafe(32)
    origin = (
        f"{urlsplit(intake['website_url']).scheme}://"
        f"{urlsplit(intake['website_url']).netloc}"
    ).rstrip("/")

    origins = [
        item.rstrip("/")
        for item in (allowed_origins or [origin])
        if item.strip()
    ]

    name = bot_name or intake["business_name"] or intake["domain"]
    prompt = system_prompt or (
        f"You are the website guide for {name}. "
        "Answer from the supplied knowledge. "
        "Do not invent missing information."
    )

    config = {
        "domain": intake["domain"],
        "name": name,
        "enabled": True,
        "key": key,
        "system_prompt": prompt,
        "model": settings.model_name,
        "model_base_url": settings.model_base_url,
        "model_api_key": settings.model_api_key,
        "allowed_origins": origins,
        "max_results": 6,
        "max_context_chars": 18000,
        "temperature": 0.1,
        "max_tokens": 900,
        "welcome_message": f"Ask a question about {name}.",
        "suggested_questions": [],
    }
    (domain_dir / "nerdo.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    public_test_url = test_url or (
        f"{settings.public_base_url}/?domain={intake['domain']}"
    )
    body = welcome_message or (
        f"Your initial Chato & Nerdo is ready.\n\n"
        f"Test it here:\n{public_test_url}\n\n"
        "The activated knowledge includes Chato's reviewed corpus summary "
        "and the canonical website pages. Reply with corrections or missing information."
    )

    send_email(
        to_email=intake["email"],
        subject=welcome_subject,
        body=body,
    )

    execute(
        '''
        UPDATE intakes
        SET status = 'active', updated_at = ?
        WHERE id = ?
        ''',
        (utc_now(), intake_id),
    )

    return {
        "domain": intake["domain"],
        "user_slug": user_slug,
        "key": key,
        "test_url": public_test_url,
    }
