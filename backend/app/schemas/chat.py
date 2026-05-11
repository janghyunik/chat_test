from __future__ import annotations

from datetime import datetime
from typing import Any, List
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    process: str = "MP"
    reference_doc_count: int = 6
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionSummary):
    messages: List[ChatMessage] = Field(default_factory=list)
    agentic_state: dict[str, Any] = Field(default_factory=dict)


class CreateChatSessionRequest(BaseModel):
    title: str | None = None
    process: str = "MP"
    reference_doc_count: int | None = None


class DeleteChatSessionResponse(BaseModel):
    deleted: bool = True
    session_id: str


class SendMessageRequest(BaseModel):
    content: str
    reference_doc_count: int | None = None


class UpdateReferenceDocCountRequest(BaseModel):
    reference_doc_count: int


class SendMessageResponse(BaseModel):
    session: ChatSessionDetail
    answer: str
