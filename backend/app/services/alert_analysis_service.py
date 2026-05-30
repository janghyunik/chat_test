from __future__ import annotations

import re
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

from app.core.config import settings
from app.services.legacy.agentic_rag_graph import answer_question_direct


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _date_key(row: dict[str, Any]) -> tuple:
    dt = _parse_dt(row.get("날짜") or row.get("date"))
    return (dt or datetime.min, str(row.get("id") or ""))


def _history_row(doc: dict[str, Any], no: int) -> dict[str, Any]:
    return {
        "no": no,
        "id": doc.get("id"),
        "date": _clean(doc.get("날짜")),
        "line": _clean(doc.get("라인")),
        "process": _clean(doc.get("공정")),
        "equipment": _clean(doc.get("설비명")),
        "error_name": _clean(doc.get("에러명")),
        "inspection": _clean(doc.get("점검이력")),
        "score": doc.get("final_score") or doc.get("structured_score") or doc.get("score"),
        "channels": doc.get("retrieval_channels", []),
    }


def _extract_action_lines(answer: str, docs: list[dict[str, Any]], limit: int = 5) -> list[str]:
    actions: list[str] = []

    # 1) LLM 답변의 조치 섹션에서 우선 추출합니다.
    section_match = re.search(
        r"##\s*조치\s*및\s*점검\s*패턴(?P<body>.*?)(?:\n##\s+|\Z)",
        answer or "",
        flags=re.S,
    )
    if section_match:
        for line in section_match.group("body").splitlines():
            line = re.sub(r"^\s*[-*\d.)]+\s*", "", line).strip()
            if line and len(line) >= 4:
                actions.append(line)
            if len(actions) >= limit:
                return actions[:limit]

    # 2) 부족하면 점검이력에서 현장 조치성 문장을 추출합니다.
    action_hints = ("교체", "조정", "확인", "점검", "청소", "수정", "리셋", "재부팅", "체결", "간격", "센서", "케이블")
    seen = {a for a in actions}
    for doc in docs:
        history = _clean(doc.get("점검이력"))
        if not history:
            continue
        # '/', '.', 줄바꿈 등을 기준으로 짧게 나눕니다.
        parts = re.split(r"[./\n]|\s{2,}", history)
        for part in parts:
            part = part.strip(" -•\t")
            if len(part) < 4:
                continue
            if not any(h in part for h in action_hints):
                continue
            if part in seen:
                continue
            seen.add(part)
            actions.append(part)
            if len(actions) >= limit:
                return actions[:limit]

    return actions[:limit]


def _estimate_confidence(docs: list[dict[str, Any]], line: str, equipment: str, error_name: str) -> str:
    if not docs:
        return "낮음"

    line_norm = _norm(line)
    equip_norm = _norm(equipment)
    error_norm = _norm(error_name)

    exact_hits = 0
    for doc in docs[:5]:
        doc_line = _norm(doc.get("라인"))
        doc_equip = _norm(doc.get("설비명"))
        doc_error = _norm(doc.get("에러명"))
        hit = 0
        if line_norm and line_norm == doc_line:
            hit += 1
        if equip_norm and (equip_norm in doc_equip or doc_equip in equip_norm):
            hit += 1
        if error_norm and (error_norm in doc_error or doc_error in error_norm):
            hit += 1
        if hit >= 2:
            exact_hits += 1

    if exact_hits >= 2 or len(docs) >= 5:
        return "높음"
    if exact_hits >= 1 or len(docs) >= 2:
        return "중간"
    return "낮음"


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _make_links(line: str, equipment: str, error_name: str, occurred_at: str | None = None) -> dict[str, str]:
    base = getattr(settings, "frontend_origin", "http://localhost:3000") or "http://localhost:3000"
    inform_query = {
        "line": line,
        "equip": equipment,
        "keyword": error_name,
    }
    # 발생일 기준 ±30일 같은 상세 조건은 alert service에서 추가해도 되지만,
    # 1차 버전에서는 담당자가 직접 기간을 조정할 수 있도록 기본 조건만 전달합니다.
    chat_seed = f"{line}라인 {equipment} {error_name} 이력과 조치 방법 알려줘"
    return {
        "inform": f"{base.rstrip('/')}/inform?{urlencode(inform_query)}",
        "chat": f"{base.rstrip('/')}?seed={urlencode({'q': chat_seed})[2:]}",
    }


def analyze_error_event(
    *,
    line: str,
    equipment: str,
    error_name: str,
    occurred_at: str | None = None,
    process: str = "MP",
    reference_doc_count: int = 5,
) -> dict[str, Any]:
    """설비 에러 이벤트를 기존 인폼노트 RAG로 분석합니다.

    이 함수는 신규 에러 감지 서비스에서 호출할 수 있는 분석 API의 핵심입니다.
    기존 채팅 세션을 만들지 않고, 인폼노트 검색/요약 로직만 재사용합니다.
    """

    started = perf_counter()
    line = _clean(line)
    equipment = _clean(equipment)
    error_name = _clean(error_name)
    process = _clean(process or "MP") or "MP"

    question = (
        f"{line}라인 {equipment} 설비에서 발생한 '{error_name}' 에러의 "
        f"과거 인폼노트 이력과 조치 및 점검 패턴을 정리해줘"
    )
    if occurred_at:
        question += f". 현재 발생 시각은 {occurred_at} 입니다."

    state = answer_question_direct(
        question=question,
        process=process,
        previous_state={},
        recent_messages=[],
        reference_doc_count=reference_doc_count,
    )

    answer = _clean(state.get("llm_response")) or "관련 인폼노트 이력을 찾지 못했습니다."
    docs = list(state.get("docs", []) or [])

    # 메일에서는 이력을 시간순으로 보기 쉽게 보여주기 위해 오래된 순으로 정렬합니다.
    sorted_docs = sorted(docs[: max(reference_doc_count, 1)], key=_date_key)
    history_rows = [_history_row(doc, idx + 1) for idx, doc in enumerate(sorted_docs)]

    actions = _extract_action_lines(answer, sorted_docs, limit=6)
    confidence = _estimate_confidence(sorted_docs, line, equipment, error_name)
    elapsed_ms = round((perf_counter() - started) * 1000, 2)

    return {
        "ok": True,
        "input": {
            "line": line,
            "equipment": equipment,
            "error_name": error_name,
            "occurred_at": occurred_at,
            "process": process,
            "reference_doc_count": reference_doc_count,
        },
        "summary": answer,
        "recommended_actions": actions,
        "history_rows": history_rows,
        "confidence": confidence,
        "elapsed_ms": elapsed_ms,
        "links": _make_links(line, equipment, error_name, occurred_at),
        "retrieval_debug": state.get("retrieval_debug", {}),
    }
