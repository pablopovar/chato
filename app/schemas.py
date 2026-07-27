from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class IntakeCreate(BaseModel):
    website_url: HttpUrl
    email: EmailStr
    business_name: str | None = Field(default=None, max_length=200)


class IntakeCreated(BaseModel):
    intake_id: str
    status_token: str
    status: str
    message: str


class IntakeStatus(BaseModel):
    intake_id: str
    website_url: str
    domain: str
    status: str
    clarification_count: int
    fetched_page_count: int = 0
    document_count: int = 0
    duplicate_count: int = 0
    chunk_count: int = 0
    created_at: str
    updated_at: str
    message: str


class ChatRequest(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    key: str = Field(min_length=8, max_length=512)
    question: str = Field(min_length=2, max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)


class SourceRecord(BaseModel):
    index: int
    title: str
    path: str
    score: float
    excerpt: str


class ChatResponse(BaseModel):
    request_id: str
    session_id: str
    domain: str
    mode: str
    model: str
    answer: str
    sources: list[SourceRecord]


class ClarificationSend(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=10000)


class DraftUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=500000)


class ActivateIntake(BaseModel):
    bot_name: str | None = Field(default=None, max_length=200)
    system_prompt: str | None = Field(default=None, max_length=20000)
    allowed_origins: list[str] = Field(default_factory=list)
    welcome_subject: str = Field(
        default="Your Chato & Nerdo is ready",
        max_length=200,
    )
    welcome_message: str | None = Field(default=None, max_length=20000)
    test_url: str | None = Field(default=None, max_length=2000)
