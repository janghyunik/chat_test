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
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionSummary):
    process: str = "MP"
    messages: List[ChatMessage] = Field(default_factory=list)
    agentic_state: dict[str, Any] = Field(default_factory=dict)


class CreateChatSessionRequest(BaseModel):
    title: str | None = None
    process: str = "MP"


class SendMessageRequest(BaseModel):
    content: str


class SendMessageResponse(BaseModel):
    session: ChatSessionDetail
    answer: str
