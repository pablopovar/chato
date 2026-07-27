from __future__ import annotations

import hmac
import re
import secrets
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from app.config import settings
from app.db import (
    enqueue_job,
    execute,
    fetch_all,
    fetch_one,
    token_hash,
    utc_now,
)
from app.schemas import (
    ActivateIntake,
    ChatRequest,
    ChatResponse,
    ClarificationSend,
    DraftUpdate,
    IntakeCreate,
    IntakeCreated,
    IntakeStatus,
    SourceRecord,
)
from app.services.chatbot import answer
from app.services.demo_catalog import enabled_demos
from app.services.intake import (
    activate_intake,
    create_intake,
    send_clarification,
)
from app.services.registry import load_bot


router = APIRouter()


def require_admin(
    x_admin_token: str = Header(default=""),
) -> None:
    if not settings.admin_token:
        raise HTTPException(
            status_code=503,
            detail="Admin API is not configured.",
        )
    if not hmac.compare_digest(
        x_admin_token,
        settings.admin_token,
    ):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


def _status_message(status_value: str) -> str:
    return {
        "queued": "Your website is queued for review.",
        "crawling": "The permitted website pages are being collected.",
        "cleaning": "The collected pages are being cleaned and consolidated.",
        "indexing": "The cleaned dataset is being indexed.",
        "interpreting": "An initial interpretation is being prepared.",
        "awaiting_review": "The initial interpretation is under human review.",
        "awaiting_clarification": (
            "A clarification was requested by email."
        ),
        "active": "Your initial Chato & Nerdo is ready.",
        "failed": "The intake needs manual attention.",
    }.get(status_value, "The intake is being processed.")


@router.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "Nerdo",
        "status": "running",
        "version": "0.2.0",
        "endpoints": {
            "intake": f"{settings.root_path}/intakes",
            "chat": f"{settings.root_path}/chat",
            "demos": f"{settings.root_path}/demos",
            "health": f"{settings.root_path}/health",
        },
    }


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "worker": "embedded",
        "database": "sqlite",
        "crawler": "robots-aware-slow-bounded",
        "cleaner": "html-to-canonical-page-documents",
        "index": "sqlite-and-files",
        "email_transport": settings.smtp_host,
    }


@router.get("/demos")
def demos() -> dict[str, list[dict]]:
    return {"demos": enabled_demos()}


@router.post(
    "/intakes",
    response_model=IntakeCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_intake(body: IntakeCreate) -> IntakeCreated:
    intake_id, status_token = create_intake(
        str(body.website_url),
        str(body.email),
        body.business_name,
    )
    return IntakeCreated(
        intake_id=intake_id,
        status_token=status_token,
        status="queued",
        message=(
            "Chato & Nerdo will review the submitted website and "
            "respond by email within 24 hours."
        ),
    )


@router.get(
    "/intakes/{intake_id}",
    response_model=IntakeStatus,
)
def intake_status(
    intake_id: str,
    x_status_token: str = Header(default=""),
) -> IntakeStatus:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake or not hmac.compare_digest(
        token_hash(x_status_token),
        intake["status_token_hash"],
    ):
        raise HTTPException(status_code=404, detail="Intake not found.")

    return IntakeStatus(
        intake_id=intake["id"],
        website_url=intake["website_url"],
        domain=intake["domain"],
        status=intake["status"],
        clarification_count=intake["clarification_count"],
        fetched_page_count=intake.get("fetched_page_count", 0),
        document_count=intake.get("document_count", 0),
        duplicate_count=intake.get("duplicate_count", 0),
        chunk_count=intake.get("chunk_count", 0),
        created_at=intake["created_at"],
        updated_at=intake["updated_at"],
        message=_status_message(intake["status"]),
    )


@router.get("/bots/{domain}")
def bot_metadata(
    domain: str,
    x_bot_key: str = Header(default=""),
) -> dict[str, Any]:
    config = load_bot(domain)
    if not config or not config.enabled:
        raise HTTPException(status_code=404, detail="Bot not found.")
    if not hmac.compare_digest(x_bot_key, config.key):
        raise HTTPException(status_code=401, detail="Invalid domain or key.")

    return {
        "domain": config.domain,
        "name": config.name,
        "welcome_message": config.welcome_message,
        "suggested_questions": list(config.suggested_questions),
    }


@router.post("/chat", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    request: Request,
    response: Response,
) -> ChatResponse:
    config = load_bot(body.domain)
    if not config or not config.enabled:
        raise HTTPException(status_code=404, detail="Bot not found.")
    if not hmac.compare_digest(body.key, config.key):
        raise HTTPException(status_code=401, detail="Invalid domain or key.")

    origin = request.headers.get("origin", "").strip().rstrip("/")
    if origin and (
        "*" not in config.allowed_origins
        and origin not in config.allowed_origins
    ):
        raise HTTPException(status_code=403, detail="Origin not allowed.")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"

    session_id = body.session_id or secrets.token_urlsafe(24)
    now = utc_now()
    existing = fetch_one(
        "SELECT id FROM conversations WHERE id = ?",
        (session_id,),
    )
    if not existing:
        execute(
            '''
            INSERT INTO conversations (id, domain, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ''',
            (session_id, config.domain, now, now),
        )
    elif fetch_one(
        "SELECT id FROM conversations WHERE id = ? AND domain = ?",
        (session_id, config.domain),
    ) is None:
        raise HTTPException(
            status_code=409,
            detail="Session belongs to another bot.",
        )

    history = fetch_all(
        '''
        SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT 8
        ''',
        (session_id,),
    )
    history.reverse()

    execute(
        '''
        INSERT INTO messages (
            conversation_id, role, content, created_at
        )
        VALUES (?, 'user', ?, ?)
        ''',
        (session_id, body.question, now),
    )

    result, mode, hits = answer(
        config,
        body.question,
        history=history,
    )

    execute(
        '''
        INSERT INTO messages (
            conversation_id, role, content, created_at
        )
        VALUES (?, 'assistant', ?, ?)
        ''',
        (session_id, result, utc_now()),
    )
    execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (utc_now(), session_id),
    )

    sources = [
        SourceRecord(
            index=index,
            title=hit.title,
            path=hit.path,
            score=hit.score,
            excerpt=(
                hit.text
                if len(hit.text) <= 600
                else hit.text[:597].rstrip() + "..."
            ),
        )
        for index, hit in enumerate(hits, start=1)
    ]

    return ChatResponse(
        request_id=str(uuid4()),
        session_id=session_id,
        domain=config.domain,
        mode=mode,
        model=config.model,
        answer=result,
        sources=sources,
    )


@router.get("/conversations/{session_id}")
def conversation(
    session_id: str,
    domain: str = Query(...),
    x_bot_key: str = Header(default=""),
) -> dict[str, Any]:
    config = load_bot(domain)
    if not config or not hmac.compare_digest(
        x_bot_key,
        config.key,
    ):
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conversation_row = fetch_one(
        '''
        SELECT * FROM conversations
        WHERE id = ? AND domain = ?
        ''',
        (session_id, config.domain),
    )
    if not conversation_row:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return {
        "session_id": session_id,
        "domain": config.domain,
        "messages": fetch_all(
            '''
            SELECT role, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id
            ''',
            (session_id,),
        ),
    }


@router.get(
    "/admin/intakes",
    dependencies=[Depends(require_admin)],
)
def admin_intakes(
    intake_status: str | None = Query(default=None, alias="status"),
) -> dict[str, list[dict]]:
    if intake_status:
        rows = fetch_all(
            '''
            SELECT * FROM intakes
            WHERE status = ?
            ORDER BY created_at DESC
            ''',
            (intake_status,),
        )
    else:
        rows = fetch_all(
            "SELECT * FROM intakes ORDER BY created_at DESC"
        )
    for row in rows:
        row.pop("status_token_hash", None)
    return {"intakes": rows}


@router.get(
    "/admin/intakes/{intake_id}",
    dependencies=[Depends(require_admin)],
)
def admin_intake(intake_id: str) -> dict[str, Any]:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found.")
    intake.pop("status_token_hash", None)

    draft = None
    if intake.get("draft_path"):
        path = Path(intake["draft_path"])
        if path.is_file():
            draft = path.read_text(encoding="utf-8", errors="replace")

    return {"intake": intake, "draft": draft}


@router.put(
    "/admin/intakes/{intake_id}/draft",
    dependencies=[Depends(require_admin)],
)
def update_draft(
    intake_id: str,
    body: DraftUpdate,
) -> dict[str, str]:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake or not intake.get("draft_path"):
        raise HTTPException(status_code=404, detail="Draft not found.")

    path = Path(intake["draft_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content.rstrip() + "\n", encoding="utf-8")
    execute(
        "UPDATE intakes SET updated_at = ? WHERE id = ?",
        (utc_now(), intake_id),
    )
    return {"status": "saved"}


@router.post(
    "/admin/intakes/{intake_id}/clarifications",
    dependencies=[Depends(require_admin)],
)
def clarification(
    intake_id: str,
    body: ClarificationSend,
) -> dict[str, str]:
    try:
        send_clarification(intake_id, body.subject, body.message)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "sent"}


@router.post(
    "/admin/intakes/{intake_id}/activate",
    dependencies=[Depends(require_admin)],
)
def activate(
    intake_id: str,
    body: ActivateIntake,
) -> dict[str, Any]:
    try:
        bot = activate_intake(
            intake_id,
            bot_name=body.bot_name,
            system_prompt=body.system_prompt,
            allowed_origins=body.allowed_origins,
            welcome_subject=body.welcome_subject,
            welcome_message=body.welcome_message,
            test_url=body.test_url,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "active", "bot": bot}



@router.get(
    "/admin/intakes/{intake_id}/dataset",
    dependencies=[Depends(require_admin)],
)
def admin_dataset(intake_id: str) -> dict[str, Any]:
    intake = fetch_one(
        "SELECT * FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found.")

    dataset = None
    crawl_run = None
    if intake.get("dataset_version_id"):
        dataset = fetch_one(
            "SELECT * FROM dataset_versions WHERE id = ?",
            (intake["dataset_version_id"],),
        )
        if dataset and dataset.get("crawl_run_id"):
            crawl_run = fetch_one(
                "SELECT * FROM crawl_runs WHERE id = ?",
                (dataset["crawl_run_id"],),
            )

    capability = fetch_one(
        "SELECT enabled FROM runtime_capabilities WHERE name = 'sqlite_fts5'"
    )
    return {
        "intake_id": intake_id,
        "status": intake["status"],
        "dataset": dataset,
        "crawl": crawl_run,
        "fts5_enabled": bool(capability and capability["enabled"]),
    }


@router.get(
    "/admin/intakes/{intake_id}/dataset/pages",
    dependencies=[Depends(require_admin)],
)
def admin_dataset_pages(
    intake_id: str,
    outcome: str | None = Query(default=None),
) -> dict[str, list[dict[str, Any]]]:
    intake = fetch_one(
        "SELECT dataset_version_id FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found.")
    dataset = fetch_one(
        "SELECT crawl_run_id FROM dataset_versions WHERE id = ?",
        (intake.get("dataset_version_id"),),
    )
    if not dataset or not dataset.get("crawl_run_id"):
        return {"pages": []}

    if outcome:
        rows = fetch_all(
            """
            SELECT * FROM crawl_pages
            WHERE crawl_run_id = ? AND outcome = ?
            ORDER BY depth, fetched_at
            """,
            (dataset["crawl_run_id"], outcome),
        )
    else:
        rows = fetch_all(
            """
            SELECT * FROM crawl_pages
            WHERE crawl_run_id = ?
            ORDER BY depth, fetched_at
            """,
            (dataset["crawl_run_id"],),
        )
    return {"pages": rows}


@router.get(
    "/admin/intakes/{intake_id}/dataset/documents",
    dependencies=[Depends(require_admin)],
)
def admin_dataset_documents(
    intake_id: str,
    include_noncanonical: bool = Query(default=True),
    include_content: bool = Query(default=False),
) -> dict[str, list[dict[str, Any]]]:
    intake = fetch_one(
        "SELECT dataset_version_id FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found.")
    version_id = intake.get("dataset_version_id")
    if not version_id:
        return {"documents": []}

    fields = "*" if include_content else (
        "id, intake_id, dataset_version_id, crawl_page_id, source_url, "
        "canonical_url, title, language, meta_description, status, "
        "duplicate_of, duplicate_reason, raw_path, clean_path, "
        "content_sha256, normalized_sha256, word_count, created_at, updated_at"
    )
    status_filter = "" if include_noncanonical else " AND status = 'canonical'"
    rows = fetch_all(
        f"""
        SELECT {fields}
        FROM documents
        WHERE dataset_version_id = ?{status_filter}
        ORDER BY status, title, source_url
        """,
        (version_id,),
    )
    return {"documents": rows}


@router.get(
    "/admin/intakes/{intake_id}/dataset/documents/{document_id}",
    dependencies=[Depends(require_admin)],
)
def admin_dataset_document(
    intake_id: str,
    document_id: str,
) -> dict[str, Any]:
    document = fetch_one(
        """
        SELECT * FROM documents
        WHERE id = ? AND intake_id = ?
        """,
        (document_id, intake_id),
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")
    chunks = fetch_all(
        """
        SELECT id, ordinal, heading, text, text_sha256, file_path
        FROM chunks
        WHERE document_id = ?
        ORDER BY ordinal
        """,
        (document_id,),
    )
    return {"document": document, "chunks": chunks}


@router.get(
    "/admin/intakes/{intake_id}/dataset/search",
    dependencies=[Depends(require_admin)],
)
def admin_dataset_search(
    intake_id: str,
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    intake = fetch_one(
        "SELECT dataset_version_id FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found.")
    version_id = intake.get("dataset_version_id")
    if not version_id:
        return {"mode": "none", "hits": []}

    terms = re.findall(r"[\wáéíóúüñç]+", q, flags=re.I)
    capability = fetch_one(
        "SELECT enabled FROM runtime_capabilities WHERE name = 'sqlite_fts5'"
    )
    if capability and capability["enabled"] and terms:
        match_query = " OR ".join(
            '"' + term.replace('"', '""') + '"'
            for term in terms
        )
        rows = fetch_all(
            """
            SELECT f.chunk_id AS id, f.document_id, f.title, f.heading,
                   f.source_url, f.body AS text, bm25(chunks_fts) AS rank
            FROM chunks_fts AS f
            JOIN chunks AS c ON c.id = f.chunk_id
            WHERE chunks_fts MATCH ? AND c.dataset_version_id = ?
            ORDER BY rank
            LIMIT ?
            """,
            (match_query, version_id, limit),
        )
        return {"mode": "sqlite-fts5", "hits": rows}

    like = "%" + q.casefold() + "%"
    rows = fetch_all(
        """
        SELECT id, document_id, title, heading, source_url, text
        FROM chunks
        WHERE dataset_version_id = ? AND lower(text) LIKE ?
        ORDER BY document_id, ordinal
        LIMIT ?
        """,
        (version_id, like, limit),
    )
    return {"mode": "sqlite-like-fallback", "hits": rows}


@router.post(
    "/admin/intakes/{intake_id}/retry",
    dependencies=[Depends(require_admin)],
)
def retry(intake_id: str) -> dict[str, str]:
    intake = fetch_one(
        "SELECT id FROM intakes WHERE id = ?",
        (intake_id,),
    )
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found.")

    execute(
        '''
        UPDATE intakes
        SET status = 'queued', error = NULL, updated_at = ?
        WHERE id = ?
        ''',
        (utc_now(), intake_id),
    )
    enqueue_job("process_intake", intake_id)
    return {"status": "queued"}
