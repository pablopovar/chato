from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, HttpUrl

from .storage import Storage, new_id, utcnow


SCOPE = "sites:start"
ISSUER = "nerdo-api"
AUDIENCE = "nerdo-operator"


class SubmissionCreate(BaseModel):
    website_url: HttpUrl
    email: EmailStr
    business_name: str | None = Field(default=None, max_length=200)


class SubmissionCreated(BaseModel):
    submission_id: str
    website_url: str
    email: str
    business_name: str | None
    status: str
    created_at: str


class StartSiteRequest(BaseModel):
    email: EmailStr


class StartedSite(BaseModel):
    submission_id: str
    site_id: str
    intake_id: str
    website_url: str
    email: str
    status: str
    intro_email_sent: bool
    intro_email_error: str | None = None


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str


def _oauth() -> dict[str, Any]:
    cfg = {
        "client_id": os.getenv("NERDO_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("NERDO_OAUTH_CLIENT_SECRET", ""),
        "signing_key": os.getenv("NERDO_OAUTH_TOKEN_SIGNING_KEY", ""),
        "scope": os.getenv("NERDO_OAUTH_SCOPE", SCOPE).strip() or SCOPE,
        "ttl": max(60, int(os.getenv("NERDO_OAUTH_ACCESS_TOKEN_TTL_SECONDS", "900"))),
    }
    missing = [
        name
        for name, value in (
            ("NERDO_OAUTH_CLIENT_ID", cfg["client_id"]),
            ("NERDO_OAUTH_CLIENT_SECRET", cfg["client_secret"]),
            ("NERDO_OAUTH_TOKEN_SIGNING_KEY", cfg["signing_key"]),
        )
        if not value
    ]
    if missing:
        raise HTTPException(503, f"OAuth is not configured: {', '.join(missing)}")
    return cfg


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def _issue_token(cfg: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": cfg["client_id"],
        "scope": cfg["scope"],
        "iat": now,
        "exp": now + cfg["ttl"],
        "jti": secrets.token_urlsafe(18),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(cfg["signing_key"].encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


def _verify_token(token: str, cfg: dict[str, Any]) -> None:
    try:
        body, supplied = token.split(".", 1)
        expected = hmac.new(cfg["signing_key"].encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64d(supplied), expected):
            raise ValueError("signature")
        payload = json.loads(_b64d(body))
        valid = (
            payload.get("iss") == ISSUER
            and payload.get("aud") == AUDIENCE
            and payload.get("sub") == cfg["client_id"]
            and int(payload.get("exp", 0)) > int(time.time())
            and SCOPE in str(payload.get("scope", "")).split()
        )
        if not valid:
            raise ValueError("claims")
    except Exception as exc:
        raise HTTPException(
            401,
            "Invalid or expired OAuth access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _basic_credentials(header: str | None) -> tuple[str, str]:
    if not header or not header.startswith("Basic "):
        raise HTTPException(
            401,
            "OAuth client authentication is required.",
            headers={"WWW-Authenticate": 'Basic realm="Nerdo OAuth client"'},
        )
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode()
        return tuple(decoded.split(":", 1))  # type: ignore[return-value]
    except Exception as exc:
        raise HTTPException(401, "Invalid OAuth client authentication.") from exc


def _init_db(storage: Storage) -> None:
    with storage.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS site_submissions (
                id TEXT PRIMARY KEY,
                website_url TEXT NOT NULL,
                email TEXT NOT NULL,
                business_name TEXT,
                domain TEXT NOT NULL,
                status TEXT NOT NULL,
                site_id TEXT,
                intro_email_status TEXT NOT NULL DEFAULT 'not_sent',
                intro_email_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_site_submissions_email_status
            ON site_submissions(email, status, created_at);
            """
        )


def _submit(storage: Storage, payload: SubmissionCreate) -> dict[str, Any]:
    submission_id = new_id("submission")
    website_url = str(payload.website_url)
    email = str(payload.email).lower()
    domain = (urlparse(website_url).hostname or "").lower().removeprefix("www.")
    now = utcnow()
    with storage.connect() as conn:
        conn.execute(
            """
            INSERT INTO site_submissions
            (id, website_url, email, business_name, domain, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?)
            """,
            (submission_id, website_url, email, payload.business_name, domain, now, now),
        )
    return {
        "submission_id": submission_id,
        "website_url": website_url,
        "email": email,
        "business_name": payload.business_name,
        "status": "pending_approval",
        "created_at": now,
    }


def _pending(storage: Storage, email: str) -> dict[str, Any] | None:
    with storage.connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM site_submissions
            WHERE lower(email) = lower(?) AND status = 'pending_approval'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (email,),
        ).fetchone()
    return dict(row) if row else None


def _update_submission(storage: Storage, submission_id: str, **changes: Any) -> None:
    changes["updated_at"] = utcnow()
    sql = ", ".join(f"{column} = ?" for column in changes)
    with storage.connect() as conn:
        conn.execute(
            f"UPDATE site_submissions SET {sql} WHERE id = ?",
            [*changes.values(), submission_id],
        )


def _intro_email(submission: dict[str, Any]) -> None:
    host = os.getenv("NERDO_SMTP_HOST", "").strip()
    from_email = os.getenv("NERDO_SMTP_FROM_EMAIL", "").strip()
    if not host or not from_email:
        raise RuntimeError("NERDO_SMTP_HOST and NERDO_SMTP_FROM_EMAIL are required.")

    message = EmailMessage()
    from_name = os.getenv("NERDO_SMTP_FROM_NAME", "Chato & Nerdo").strip()
    message["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    message["To"] = submission["email"]
    message["Subject"] = os.getenv(
        "NERDO_INTRO_EMAIL_SUBJECT",
        "Nerdo has started preparing your website",
    )
    reply_to = os.getenv("NERDO_SMTP_REPLY_TO", "").strip()
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(
        f"""Hello,

Pablo approved your request and asked Nerdo to start preparing {submission['domain']}.

What happens next:
1. Nerdo reads, cleans, and organizes the website material.
2. Pablo reviews the initial interpretation and preparation.
3. Nerdo may email you if something needs clarification.
4. Chato is made ready for the public-facing experience after review.

Reply to this email with corrections, additional source material, or questions.

Nerdo
Nerding and Technical Operations Officer
Chato & Nerdo — AI Assistants for Public Facing UIs
"""
    )

    port = int(os.getenv("NERDO_SMTP_PORT", "25"))
    mode = os.getenv("NERDO_SMTP_TLS_MODE", "none").lower()
    timeout = float(os.getenv("NERDO_SMTP_TIMEOUT_SECONDS", "20"))
    username = os.getenv("NERDO_SMTP_USERNAME", "").strip()
    password = os.getenv("NERDO_SMTP_PASSWORD", "")
    if mode == "ssl":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
    with smtp:
        smtp.ehlo()
        if mode == "starttls":
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        elif mode not in {"none", "ssl"}:
            raise RuntimeError("NERDO_SMTP_TLS_MODE must be none, starttls, or ssl.")
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def _remove_immediate_start_route(app: FastAPI) -> None:
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/v1/sites"
            and "POST" in (getattr(route, "methods", set()) or set())
        )
    ]


def install_manual_start(app: FastAPI, service: Any, storage: Storage) -> None:
    _init_db(storage)
    _remove_immediate_start_route(app)

    async def oauth_token(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> OAuthTokenResponse:
        cfg = _oauth()
        client_id, client_secret = _basic_credentials(authorization)
        if not (
            hmac.compare_digest(client_id, cfg["client_id"])
            and hmac.compare_digest(client_secret, cfg["client_secret"])
        ):
            raise HTTPException(401, "Invalid OAuth client credentials.")
        form = parse_qs((await request.body()).decode(), keep_blank_values=True)
        if (form.get("grant_type") or [""])[0] != "client_credentials":
            raise HTTPException(400, "unsupported_grant_type")
        requested_scope = (form.get("scope") or [cfg["scope"]])[0]
        if requested_scope != cfg["scope"]:
            raise HTTPException(400, "invalid_scope")
        return OAuthTokenResponse(
            access_token=_issue_token(cfg),
            expires_in=cfg["ttl"],
            scope=cfg["scope"],
        )

    def submit_site(payload: SubmissionCreate) -> SubmissionCreated:
        return SubmissionCreated.model_validate(_submit(storage, payload))

    def start_site(
        payload: StartSiteRequest,
        authorization: str | None = Header(default=None),
    ) -> StartedSite:
        cfg = _oauth()
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "OAuth bearer token is required.")
        _verify_token(authorization[7:], cfg)

        submission = _pending(storage, str(payload.email))
        if submission is None:
            raise HTTPException(404, "No pending Chato submission exists for that email address.")

        _update_submission(storage, submission["id"], status="starting")
        try:
            site, _site_token = service.create_site(
                submission["website_url"],
                submission["email"],
                submission["business_name"],
            )
        except Exception:
            _update_submission(storage, submission["id"], status="pending_approval")
            raise

        _update_submission(storage, submission["id"], status="started", site_id=site["id"])
        sent = False
        email_error = None
        try:
            _intro_email(submission)
            sent = True
            _update_submission(
                storage,
                submission["id"],
                intro_email_status="sent",
                intro_email_error=None,
            )
        except Exception as exc:
            email_error = str(exc)
            _update_submission(
                storage,
                submission["id"],
                intro_email_status="failed",
                intro_email_error=email_error,
            )

        return StartedSite(
            submission_id=submission["id"],
            site_id=site["id"],
            intake_id=site["intake_id"],
            website_url=submission["website_url"],
            email=submission["email"],
            status=site["status"],
            intro_email_sent=sent,
            intro_email_error=email_error,
        )

    app.add_api_route(
        "/oauth/token",
        oauth_token,
        methods=["POST"],
        response_model=OAuthTokenResponse,
        tags=["OAuth"],
        summary="Issue Pablo CLI access token",
    )
    app.add_api_route(
        "/v1/sites",
        submit_site,
        methods=["POST"],
        response_model=SubmissionCreated,
        status_code=status.HTTP_201_CREATED,
        tags=["Chato"],
        summary="Submit website for manual approval",
    )
    app.add_api_route(
        "/v1/operator/sites/start",
        start_site,
        methods=["POST"],
        response_model=StartedSite,
        tags=["Nerdo operator"],
        summary="Approve and start a submitted website by email",
    )
    app.openapi_schema = None
