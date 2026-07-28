from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.admin_domains import router as admin_domains_router
from app.admin_history import router as admin_history_router
from app.admin_setup_report import router as admin_setup_report_router
from app.api import router
from app.chat_trace_install import install_chat_tracing
from app.config import settings
from app.db import init_db
from app.services.registry import all_allowed_origins
from app.services.worker import start_worker, stop_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    start_worker()
    try:
        yield
    finally:
        stop_worker()


app = FastAPI(
    title="Nerdo",
    description=(
        "Intake, bounded crawling, cleaning, file/database indexing, interpretation, email, and grounded-chat API."
    ),
    version="0.2.0",
    root_path=settings.root_path,
    lifespan=lifespan,
)


@app.middleware("http")
async def cors(
    request: Request,
    call_next,
) -> Response:
    origin = request.headers.get("origin", "").strip().rstrip("/")
    allowed = origin in all_allowed_origins() if origin else False

    if request.method == "OPTIONS":
        if not allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin is not allowed."},
            )
        response = Response(status_code=204)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PUT, OPTIONS"
        )
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Status-Token, X-Admin-Token, X-Bot-Key"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = "Origin"
        return response

    response = await call_next(request)
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


app.include_router(router)
app.include_router(admin_history_router)
app.include_router(admin_domains_router)
app.include_router(admin_setup_report_router)
install_chat_tracing(app)
