from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.services.alert_analysis_service import analyze_error_event

router = APIRouter(prefix="/api/alert", tags=["alert"])


def _check_alert_api_key(x_alert_api_key: Optional[str]) -> None:
    expected = os.getenv("ALERT_API_KEY", "").strip()
    if not expected:
        return
    if x_alert_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="alert API key가 올바르지 않습니다.",
        )


class AlertAnalyzeRequest(BaseModel):
    line: str = Field(..., description="라인명. 예: 2, 2L, C5")
    equipment: str = Field(..., description="설비명. 예: ATPS-1L02")
    error_name: str = Field(..., description="에러명/알람명/정비사항")
    occurred_at: str | None = Field(None, description="현재 에러 발생 시각")
    process: str = Field("MP", description="인폼노트 검색 대상 공정")
    reference_doc_count: int = Field(5, ge=1, le=30, description="참조 문서 수")


@router.post("/analyze-error")
def analyze_error(payload: AlertAnalyzeRequest, x_alert_api_key: Optional[str] = Header(None)):
    """설비 에러 이벤트를 기존 인폼노트 RAG 로직으로 분석합니다.

    신규 에러 감지/메일링 서비스가 이 API를 호출하여 과거 이력, 조치 패턴,
    담당자 메일 본문에 들어갈 요약 데이터를 받아갑니다.
    """
    _check_alert_api_key(x_alert_api_key)
    return analyze_error_event(
        line=payload.line,
        equipment=payload.equipment,
        error_name=payload.error_name,
        occurred_at=payload.occurred_at,
        process=payload.process,
        reference_doc_count=payload.reference_doc_count,
    )
