from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip().rstrip("/")
        for item in os.getenv(name, default).split(",")
        if item.strip()
    )


@dataclass(frozen=True)
class Settings:
    root_path: str
    data_dir: Path
    users_dir: Path
    demos_path: Path
    admin_token: str
    public_origins: tuple[str, ...]
    public_base_url: str

    crawl_max_pages: int
    crawl_max_attempts: int
    crawl_max_depth: int
    crawl_max_links_per_page: int
    crawl_min_delay_seconds: float
    crawl_max_delay_seconds: float
    crawl_max_duration_seconds: float
    crawl_timeout_seconds: float
    crawl_max_page_bytes: int
    crawl_max_total_bytes: int
    crawl_max_redirects: int
    crawl_user_agent: str
    crawl_allow_subdomains: bool
    clean_min_document_chars: int
    clean_near_duplicate_threshold: float
    index_chunk_chars: int
    index_chunk_overlap: int
    max_clarification_emails: int
    worker_poll_seconds: float

    model_base_url: str
    model_api_key: str
    model_name: str
    model_timeout_seconds: float
    model_max_tokens: int

    smtp_host: str
    smtp_port: int
    smtp_tls_mode: str
    smtp_username: str
    smtp_password: str
    smtp_from_email: str
    smtp_from_name: str


def _bool(name: str, default: str = "false") -> bool:
    value = os.getenv(name, default).strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def load_settings() -> Settings:
    root_path = os.getenv("NERDO_ROOT_PATH", "/api").rstrip("/")
    data_dir = Path(os.getenv("NERDO_DATA_DIR", "/app/data")).resolve()
    users_dir = Path(os.getenv("NERDO_USERS_DIR", "/app/users")).resolve()
    demos_path = Path(
        os.getenv("NERDO_DEMOS_PATH", "/app/demos.toml")
    ).resolve()

    settings = Settings(
        root_path=root_path,
        data_dir=data_dir,
        users_dir=users_dir,
        demos_path=demos_path,
        admin_token=os.getenv("NERDO_ADMIN_TOKEN", "").strip(),
        public_origins=_csv("NERDO_PUBLIC_ORIGINS"),
        public_base_url=os.getenv(
            "NERDO_PUBLIC_BASE_URL",
            "https://chato.povarchik.com",
        ).rstrip("/"),
        crawl_max_pages=int(os.getenv("NERDO_CRAWL_MAX_PAGES", "24")),
        crawl_max_attempts=int(
            os.getenv("NERDO_CRAWL_MAX_ATTEMPTS", "60")
        ),
        crawl_max_depth=int(os.getenv("NERDO_CRAWL_MAX_DEPTH", "4")),
        crawl_max_links_per_page=int(
            os.getenv("NERDO_CRAWL_MAX_LINKS_PER_PAGE", "120")
        ),
        crawl_min_delay_seconds=float(
            os.getenv("NERDO_CRAWL_MIN_DELAY_SECONDS", "2.5")
        ),
        crawl_max_delay_seconds=float(
            os.getenv("NERDO_CRAWL_MAX_DELAY_SECONDS", "6.5")
        ),
        crawl_max_duration_seconds=float(
            os.getenv("NERDO_CRAWL_MAX_DURATION_SECONDS", "900")
        ),
        crawl_timeout_seconds=float(
            os.getenv("NERDO_CRAWL_TIMEOUT_SECONDS", "25")
        ),
        crawl_max_page_bytes=int(
            os.getenv("NERDO_CRAWL_MAX_PAGE_BYTES", "2000000")
        ),
        crawl_max_total_bytes=int(
            os.getenv("NERDO_CRAWL_MAX_TOTAL_BYTES", "24000000")
        ),
        crawl_max_redirects=int(
            os.getenv("NERDO_CRAWL_MAX_REDIRECTS", "6")
        ),
        crawl_user_agent=os.getenv(
            "NERDO_CRAWL_USER_AGENT",
            "Nerdo/0.2",
        ),
        crawl_allow_subdomains=_bool(
            "NERDO_CRAWL_ALLOW_SUBDOMAINS",
            "false",
        ),
        clean_min_document_chars=int(
            os.getenv("NERDO_CLEAN_MIN_DOCUMENT_CHARS", "120")
        ),
        clean_near_duplicate_threshold=float(
            os.getenv(
                "NERDO_CLEAN_NEAR_DUPLICATE_THRESHOLD",
                "0.92",
            )
        ),
        index_chunk_chars=int(
            os.getenv("NERDO_INDEX_CHUNK_CHARS", "1800")
        ),
        index_chunk_overlap=int(
            os.getenv("NERDO_INDEX_CHUNK_OVERLAP", "180")
        ),
        max_clarification_emails=int(
            os.getenv("NERDO_MAX_CLARIFICATION_EMAILS", "3")
        ),
        worker_poll_seconds=float(
            os.getenv("NERDO_WORKER_POLL_SECONDS", "3")
        ),
        model_base_url=os.getenv(
            "NERDO_MODEL_BASE_URL",
            "http://host.docker.internal:11434/v1",
        ).rstrip("/"),
        model_api_key=os.getenv("NERDO_MODEL_API_KEY", "ollama"),
        model_name=os.getenv("NERDO_MODEL_NAME", "qwen3.5:latest"),
        model_timeout_seconds=float(
            os.getenv("NERDO_MODEL_TIMEOUT_SECONDS", "600")
        ),
        model_max_tokens=int(
            os.getenv("NERDO_MODEL_MAX_TOKENS", "1200")
        ),
        smtp_host=os.getenv("NERDO_SMTP_HOST", "hq3-mx-10"),
        smtp_port=int(os.getenv("NERDO_SMTP_PORT", "25")),
        smtp_tls_mode=os.getenv(
            "NERDO_SMTP_TLS_MODE",
            "none",
        ).strip().casefold(),
        smtp_username=os.getenv("NERDO_SMTP_USERNAME", ""),
        smtp_password=os.getenv("NERDO_SMTP_PASSWORD", ""),
        smtp_from_email=os.getenv("NERDO_SMTP_FROM_EMAIL", ""),
        smtp_from_name=os.getenv(
            "NERDO_SMTP_FROM_NAME",
            "Chato & Nerdo",
        ),
    )

    if settings.smtp_tls_mode not in {"none", "starttls", "ssl"}:
        raise RuntimeError(
            "NERDO_SMTP_TLS_MODE must be none, starttls, or ssl."
        )
    if settings.crawl_min_delay_seconds < 0:
        raise RuntimeError("NERDO_CRAWL_MIN_DELAY_SECONDS must be >= 0.")
    if settings.crawl_max_delay_seconds < settings.crawl_min_delay_seconds:
        raise RuntimeError(
            "NERDO_CRAWL_MAX_DELAY_SECONDS must be >= "
            "NERDO_CRAWL_MIN_DELAY_SECONDS."
        )
    if not 0.5 <= settings.clean_near_duplicate_threshold <= 1.0:
        raise RuntimeError(
            "NERDO_CLEAN_NEAR_DUPLICATE_THRESHOLD must be between "
            "0.5 and 1.0."
        )
    if not 0 <= settings.index_chunk_overlap < settings.index_chunk_chars:
        raise RuntimeError(
            "NERDO_INDEX_CHUNK_OVERLAP must be >= 0 and smaller than "
            "NERDO_INDEX_CHUNK_CHARS."
        )

    return settings


settings = load_settings()
