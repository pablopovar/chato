from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl

Persona = Literal["chato", "nerdo"]
OperationKind = Literal[
    "sources.refresh",
    "sources.changes",
    "knowledge.contradictions",
    "answers.diagnose",
    "answers.correct",
    "integrations.connect",
    "integrations.verify",
    "integrations.disconnect",
]
OperationStatus = Literal[
    "accepted",
    "running",
    "completed",
    "failed",
    "needs_input",
    "blocked",
]


class SiteCreate(BaseModel):
    website_url: HttpUrl
    email: EmailStr
    business_name: str | None = Field(default=None, max_length=200)


class SiteCreated(BaseModel):
    site_id: str
    site_token: str
    intake_id: str
    status: str


class SiteStatus(BaseModel):
    site_id: str
    website_url: str
    email: str
    business_name: str | None
    domain: str
    status: str
    nerdo: dict[str, Any] | None = None


class ActivationAttach(BaseModel):
    domain: str = Field(min_length=1, max_length=255)
    bot_key: str = Field(min_length=1)


class ConversationCreate(BaseModel):
    persona: Persona = "chato"
    site_id: str | None = None


class ConversationCreated(BaseModel):
    conversation_id: str
    persona: Persona
    site_id: str | None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    context: dict[str, Any] = Field(default_factory=dict)


class MessageRecord(BaseModel):
    message_id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    persona: Persona
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class MessageReply(BaseModel):
    message: MessageRecord
    operation_id: str | None = None
    needs_input: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class OperationRecord(BaseModel):
    operation_id: str
    site_id: str
    kind: OperationKind
    status: OperationStatus
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str


class ChangesRequest(BaseModel):
    capture_current: bool = True


class ContradictionRequest(BaseModel):
    minimum_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=500)


class DiagnoseAnswerRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20_000)
    answer: str = Field(min_length=1, max_length=50_000)
    search_limit: int = Field(default=10, ge=1, le=50)


class AnswerCorrectionRequest(BaseModel):
    answer_id: str | None = None
    question: str = Field(min_length=1, max_length=20_000)
    original_answer: str = Field(min_length=1, max_length=50_000)
    correction: str = Field(min_length=1, max_length=50_000)


IntegrationKind = Literal[
    "generic_web",
    "wordpress",
    "joomla",
    "slack",
    "whatsapp",
    "sms",
    "notion",
    "email",
]


class IntegrationCreate(BaseModel):
    kind: IntegrationKind
    target_url: HttpUrl | None = None
    label: str | None = Field(default=None, max_length=200)
    configuration: dict[str, Any] = Field(default_factory=dict)


class IntegrationUpdate(BaseModel):
    target_url: HttpUrl | None = None
    label: str | None = Field(default=None, max_length=200)
    configuration: dict[str, Any] | None = None


class IntegrationRecord(BaseModel):
    integration_id: str
    site_id: str
    kind: IntegrationKind
    target_url: str | None
    label: str | None
    status: str
    configuration: dict[str, Any]
    verification: dict[str, Any]
    created_at: str
    updated_at: str


class CapabilityRecord(BaseModel):
    name: str
    persona: Persona
    description: str
    endpoint: str
    implementation: Literal["implemented", "bridge", "adapter-required"]
