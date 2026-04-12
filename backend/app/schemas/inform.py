from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class InformRecord(BaseModel):
    No: int
    날짜: Any = ""
    라인: str = ""
    공정: str = ""
    설비명: str = ""
    에러명: str = ""
    점검이력: str = ""
    중복수: int | None = None


class InformListResponse(BaseModel):
    data: list[InformRecord] = Field(default_factory=list)
    full: list[dict[str, Any]] = Field(default_factory=list)
    options: dict[str, list[str]] = Field(default_factory=dict)
