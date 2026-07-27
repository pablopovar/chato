from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status

from .config import Settings, settings as default_settings
from .nerdo_client import NerdoClient, NerdoError
from .models import (
    ActivationAttach,
    AnswerCorrectionRequest,
    CapabilityRecord,
    ChangesRequest,
    ContradictionRequest,
    ConversationCreate,
    ConversationCreated,
    DiagnoseAnswerRequest,
    IntegrationCreate,
    IntegrationRecord,
    IntegrationUpdate,
    MessageCreate,
    MessageRecord,
    MessageReply,
    OperationRecord,
    SiteCreate,
    SiteCreated,
    SiteStatus,
)
from .service import GatewayService
from .storage import Storage


def create_app(settings: Settings | None = None, storage: Storage | None = None,
               nerdo: NerdoClient | None = None) -> FastAPI:
    cfg = settings or default_settings
    db = storage or Storage(cfg.database_path)
    nerdo_client = nerdo or NerdoClient(cfg.core_base_url, cfg.core_admin_token, cfg.request_timeout_seconds)
    service = GatewayService(cfg, db, nerdo_client)

    app = FastAPI(
        title="Chato & Nerdo API",
        version="1.0.0",
        description=(
            "Chato handles the public conversation. Nerdo handles website knowledge, "
            "answer diagnostics, integrations, and technical operations."
        ),
    )
    app.state.settings = cfg
    app.state.storage = db
    app.state.nerdo = nerdo_client
    app.state.service = service

    def operator_auth(x_nerdo_key: Annotated[str | None, Header()] = None) -> None:
        if not x_nerdo_key or not hmac.compare_digest(x_nerdo_key, cfg.operator_token):
            raise HTTPException(status_code=401, detail="A valid X-Nerdo-Key is required.")

    def get_site(site_id: str) -> dict[str, Any]:
        site = db.get_site(site_id)
        if site is None:
            raise HTTPException(status_code=404, detail="Site not found.")
        return site

    def site_access(site_id: str, x_site_token: Annotated[str | None, Header()] = None,
                    x_nerdo_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        site = get_site(site_id)
        operator_ok = bool(x_nerdo_key and hmac.compare_digest(x_nerdo_key, cfg.operator_token))
        site_ok = bool(x_site_token and db.verify_site_token(site_id, x_site_token))
        if not (operator_ok or site_ok):
            raise HTTPException(status_code=401, detail="A valid X-Site-Token or X-Nerdo-Key is required.")
        return site

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "nerdo-api", "version": "1.0.0"}

    @app.get("/v1/capabilities", response_model=list[CapabilityRecord])
    def capabilities() -> list[dict[str, str]]:
        return [
            {"name": "public.conversation", "persona": "chato", "description": "Explain the product and answer from an activated website assistant.", "endpoint": "POST /v1/conversations/{id}/messages", "implementation": "bridge"},
            {"name": "sources.refresh", "persona": "nerdo", "description": "Re-run website collection and preparation.", "endpoint": "POST /v1/sites/{site_id}/sources/refresh", "implementation": "bridge"},
            {"name": "sources.changes", "persona": "nerdo", "description": "Compare current source documents with the prior snapshot.", "endpoint": "POST /v1/sites/{site_id}/sources/changes", "implementation": "implemented"},
            {"name": "knowledge.contradictions", "persona": "nerdo", "description": "Flag possible conflicting statements for human review.", "endpoint": "POST /v1/sites/{site_id}/knowledge/contradictions", "implementation": "implemented"},
            {"name": "answers.diagnose", "persona": "nerdo", "description": "Audit an answer against retrieved website evidence.", "endpoint": "POST /v1/sites/{site_id}/answers/diagnose", "implementation": "implemented"},
            {"name": "integrations.connect", "persona": "nerdo", "description": "Create website or provider integration configuration.", "endpoint": "POST /v1/sites/{site_id}/integrations", "implementation": "implemented"},
            {"name": "integrations.verify", "persona": "nerdo", "description": "Verify web installation markers or call a provider adapter.", "endpoint": "POST /v1/sites/{site_id}/integrations/{integration_id}/verify", "implementation": "implemented"},
        ]

    @app.post("/v1/sites", response_model=SiteCreated, status_code=201)
    def create_site(payload: SiteCreate) -> dict[str, Any]:
        try:
            site, token = service.create_site(str(payload.website_url), str(payload.email), payload.business_name)
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"site_id": site["id"], "site_token": token, "intake_id": site["intake_id"], "status": site["status"]}

    @app.get("/v1/sites/{site_id}", response_model=SiteStatus)
    def read_site(site_id: str,
                  x_site_token: Annotated[str | None, Header()] = None,
                  x_nerdo_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        site = site_access(site_id, x_site_token, x_nerdo_key)
        try:
            return service.site_status(site)
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/sites/{site_id}/activation", response_model=SiteStatus,
              dependencies=[Depends(operator_auth)])
    def attach_activation(site_id: str, payload: ActivationAttach) -> dict[str, Any]:
        site = get_site(site_id)
        site = db.update_site(site_id, domain=payload.domain, bot_key=payload.bot_key, status="active")
        return {
            "site_id": site["id"], "website_url": site["website_url"], "email": site["email"],
            "business_name": site["business_name"], "domain": site["domain"], "status": site["status"], "nerdo": None,
        }

    @app.post("/v1/conversations", response_model=ConversationCreated, status_code=201)
    def create_conversation(payload: ConversationCreate,
                            x_site_token: Annotated[str | None, Header()] = None,
                            x_nerdo_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        if payload.site_id:
            site_access(payload.site_id, x_site_token, x_nerdo_key)
        if payload.persona == "nerdo":
            operator_auth(x_nerdo_key)
        conversation = db.create_conversation(payload.persona, payload.site_id)
        return {"conversation_id": conversation["id"], "persona": conversation["persona"], "site_id": conversation["site_id"]}

    @app.post("/v1/conversations/{conversation_id}/messages", response_model=MessageReply)
    def send_message(conversation_id: str, payload: MessageCreate,
                     x_site_token: Annotated[str | None, Header()] = None,
                     x_nerdo_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        conversation = db.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if conversation.get("site_id"):
            site_access(conversation["site_id"], x_site_token, x_nerdo_key)
        if conversation["persona"] == "nerdo":
            operator_auth(x_nerdo_key)
        db.add_message(conversation_id, "user", conversation["persona"], payload.content, payload.context)
        try:
            if conversation["persona"] == "chato":
                text, data = service.chato_message(conversation, payload.content)
                operation = None
                needs_input: list[str] = []
            else:
                text, data, operation, needs_input = service.nerdo_message(conversation, payload.content, payload.context)
        except (NerdoError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        assistant = db.add_message(conversation_id, "assistant", conversation["persona"], text, data)
        return {
            "message": assistant,
            "operation_id": operation["operation_id"] if operation else None,
            "needs_input": needs_input,
            "actions": data.get("actions", []) if isinstance(data, dict) else [],
        }

    @app.get("/v1/conversations/{conversation_id}/messages", response_model=list[MessageRecord])
    def list_messages(conversation_id: str,
                      x_site_token: Annotated[str | None, Header()] = None,
                      x_nerdo_key: Annotated[str | None, Header()] = None) -> list[dict[str, Any]]:
        conversation = db.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if conversation.get("site_id"):
            site_access(conversation["site_id"], x_site_token, x_nerdo_key)
        if conversation["persona"] == "nerdo":
            operator_auth(x_nerdo_key)
        return db.list_messages(conversation_id)

    @app.get("/v1/sites/{site_id}/sources", dependencies=[Depends(operator_auth)])
    def list_sources(site_id: str) -> dict[str, Any]:
        site = get_site(site_id)
        try:
            documents = service.list_sources(site)
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"site_id": site_id, "count": len(documents), "documents": documents}

    @app.post("/v1/sites/{site_id}/sources/refresh", response_model=OperationRecord,
              status_code=202, dependencies=[Depends(operator_auth)])
    def refresh_sources(site_id: str) -> dict[str, Any]:
        try:
            return service.refresh_sources(get_site(site_id))
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/sites/{site_id}/sources/changes", response_model=OperationRecord,
              dependencies=[Depends(operator_auth)])
    def source_changes(site_id: str, payload: ChangesRequest) -> dict[str, Any]:
        try:
            result = service.source_changes(get_site(site_id), payload.capture_current)
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return db.create_operation(site_id, "sources.changes", "completed", result=result)

    @app.post("/v1/sites/{site_id}/knowledge/contradictions", response_model=OperationRecord,
              dependencies=[Depends(operator_auth)])
    def contradictions(site_id: str, payload: ContradictionRequest) -> dict[str, Any]:
        try:
            result = service.contradictions(get_site(site_id), payload.limit)
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        result["findings"] = [item for item in result["findings"] if item["confidence"] >= payload.minimum_confidence]
        result["finding_count"] = len(result["findings"])
        return db.create_operation(site_id, "knowledge.contradictions", "completed", result=result)

    @app.post("/v1/sites/{site_id}/answers/diagnose", response_model=OperationRecord,
              dependencies=[Depends(operator_auth)])
    def diagnose(site_id: str, payload: DiagnoseAnswerRequest) -> dict[str, Any]:
        try:
            result = service.diagnose(get_site(site_id), payload.question, payload.answer, payload.search_limit)
        except NerdoError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return db.create_operation(site_id, "answers.diagnose", "completed", result=result)

    @app.post("/v1/sites/{site_id}/answers/corrections", response_model=OperationRecord,
              dependencies=[Depends(operator_auth)])
    def correct(site_id: str, payload: AnswerCorrectionRequest) -> dict[str, Any]:
        site = get_site(site_id)
        result = service.correct_answer(
            site,
            answer_id=payload.answer_id,
            question=payload.question,
            original_answer=payload.original_answer,
            correction=payload.correction,
        )
        return db.create_operation(site_id, "answers.correct", "completed", result=result)

    @app.get("/v1/sites/{site_id}/integrations", response_model=list[IntegrationRecord],
             dependencies=[Depends(operator_auth)])
    def integrations(site_id: str) -> list[dict[str, Any]]:
        get_site(site_id)
        return db.list_integrations(site_id)

    @app.post("/v1/sites/{site_id}/integrations", response_model=IntegrationRecord,
              status_code=201, dependencies=[Depends(operator_auth)])
    def connect(site_id: str, payload: IntegrationCreate) -> dict[str, Any]:
        site = get_site(site_id)
        return service.connect_integration(
            site, payload.kind, str(payload.target_url) if payload.target_url else None,
            payload.label, payload.configuration,
        )

    @app.patch("/v1/sites/{site_id}/integrations/{integration_id}", response_model=IntegrationRecord,
               dependencies=[Depends(operator_auth)])
    def update_integration(site_id: str, integration_id: str, payload: IntegrationUpdate) -> dict[str, Any]:
        get_site(site_id)
        current = db.get_integration(integration_id)
        if current is None or current["site_id"] != site_id:
            raise HTTPException(status_code=404, detail="Integration not found.")
        return db.update_integration(
            integration_id,
            target_url=str(payload.target_url) if payload.target_url else None,
            label=payload.label,
            configuration=payload.configuration,
        )

    @app.delete("/v1/sites/{site_id}/integrations/{integration_id}", status_code=204,
                dependencies=[Depends(operator_auth)])
    def disconnect(site_id: str, integration_id: str) -> Response:
        get_site(site_id)
        current = db.get_integration(integration_id)
        if current is None or current["site_id"] != site_id:
            raise HTTPException(status_code=404, detail="Integration not found.")
        db.delete_integration(integration_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/sites/{site_id}/integrations/{integration_id}/verify",
              response_model=IntegrationRecord, dependencies=[Depends(operator_auth)])
    def verify(site_id: str, integration_id: str) -> dict[str, Any]:
        get_site(site_id)
        integration = db.get_integration(integration_id)
        if integration is None or integration["site_id"] != site_id:
            raise HTTPException(status_code=404, detail="Integration not found.")
        try:
            return service.verify_integration(integration)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Verification failed: {exc}") from exc

    @app.get("/v1/operations/{operation_id}", response_model=OperationRecord,
             dependencies=[Depends(operator_auth)])
    def operation(operation_id: str) -> dict[str, Any]:
        current = db.get_operation(operation_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Operation not found.")
        return service.poll_operation(current)

    return app


app = create_app()
