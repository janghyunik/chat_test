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
    중복키: str | None = None


class InformPageInfo(BaseModel):
    page: int = 1
    page_size: int = 20
    total_items: int = 0
    total_pages: int = 0
    block_size: int = 9
    block_start: int = 1
    block_end: int = 1
    has_prev_block: bool = False
    has_next_block: bool = False


class InformListResponse(BaseModel):
    data: list[InformRecord] = Field(default_factory=list)
    full: list[dict[str, Any]] = Field(default_factory=list)
    options: dict[str, list[str]] = Field(default_factory=dict)
    page_info: InformPageInfo = Field(default_factory=InformPageInfo)
