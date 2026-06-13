from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

from dotenv import load_dotenv
from langchain_community.chat_models.ollama import ChatOllama
from langchain.schema import HumanMessage

from app.core.config import settings
from app.services.legacy.pg_vector_utils import search_alert_precision_documents

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL")
alert_llm = ChatOllama(model=LLM_MODEL, temperature=0, stream=False)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


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
        "match_level": _clean(doc.get("match_level")) or "관련 이력",
        "score": doc.get("final_score") or doc.get("alert_score") or doc.get("structured_score") or doc.get("score"),
        "channels": doc.get("retrieval_channels", []),
    }


def _doc_line(doc: dict[str, Any], idx: int) -> str:
    return (
        f"[# {idx}] 날짜={_clean(doc.get('날짜'))} | 라인={_clean(doc.get('라인'))} | "
        f"설비명={_clean(doc.get('설비명'))} | 에러명={_clean(doc.get('에러명'))} | "
        f"매칭수준={_clean(doc.get('match_level')) or '관련 이력'} | "
        f"점검이력={_clean(doc.get('점검이력'))}"
    )


def _build_evidence(docs: list[dict[str, Any]], limit: int = 12) -> str:
    if not docs:
        return "관련 이력이 검색되지 않았습니다."
    return "\n".join(_doc_line(doc, idx) for idx, doc in enumerate(docs[:limit], start=1))


def _match_stats(docs: list[dict[str, Any]], line: str, equipment: str, error_name: str) -> dict[str, Any]:
    line_norm = _norm(line)
    equip_norm = _norm(equipment)
    error_norm = _norm(error_name)
    same_equipment = 0
    same_line_equipment = 0
    error_like = 0
    model_counts: Counter[str] = Counter()

    for doc in docs:
        dl = _norm(doc.get("라인"))
        de = _norm(doc.get("설비명"))
        der = _norm(doc.get("에러명"))
        if equip_norm and (equip_norm == de or equip_norm in de or de in equip_norm):
            same_equipment += 1
            if line_norm and line_norm == dl:
                same_line_equipment += 1
        if error_norm and (error_norm in der or der in error_norm):
            error_like += 1
        model = re.match(r"^([a-z0-9]+)", de or "")
        if model:
            model_counts[model.group(1).upper()] += 1

    recent_docs = sorted(docs, key=_date_key, reverse=True)
    return {
        "same_equipment_count": same_equipment,
        "same_line_equipment_count": same_line_equipment,
        "error_like_count": error_like,
        "top_model": model_counts.most_common(1)[0][0] if model_counts else "",
        "latest_date": _clean(recent_docs[0].get("날짜")) if recent_docs else "",
        "doc_count": len(docs),
    }


def _estimate_confidence(docs: list[dict[str, Any]], line: str, equipment: str, error_name: str) -> str:
    if not docs:
        return "낮음"
    stats = _match_stats(docs, line, equipment, error_name)
    if stats["same_line_equipment_count"] >= 2 and (stats["error_like_count"] >= 1 or len(docs) >= 5):
        return "높음"
    if stats["same_equipment_count"] >= 1 or stats["error_like_count"] >= 1:
        return "중간"
    return "낮음"


def _make_links(line: str, equipment: str, error_name: str, occurred_at: str | None = None) -> dict[str, str]:
    base = getattr(settings, "frontend_origin", "http://localhost:3000") or "http://localhost:3000"
    inform_query = {
        "line": line,
        "equip": equipment,
        "keyword": error_name,
    }
    chat_seed = f"{line}라인 {equipment} {error_name} 이력과 조치 방법 알려줘"
    return {
        "inform": f"{base.rstrip('/')}/inform?{urlencode(inform_query)}",
        "chat": f"{base.rstrip('/')}?seed={urlencode({'q': chat_seed})[2:]}",
    }


def _extract_action_lines(answer: str, docs: list[dict[str, Any]], limit: int = 6) -> list[str]:
    actions: list[str] = []
    section_match = re.search(
        r"##\s*(?:우선\s*)?점검\s*(?:항목|체크리스트)|##\s*조치\s*및\s*점검\s*패턴",
        answer or "",
    )
    if section_match:
        start = section_match.end()
        tail = answer[start:]
        next_section = re.search(r"\n##\s+", tail)
        body = tail[: next_section.start()] if next_section else tail
        for line in body.splitlines():
            line = re.sub(r"^\s*[-*•\d.)]+\s*", "", line).strip()
            if line and len(line) >= 4:
                actions.append(line)
            if len(actions) >= limit:
                return actions[:limit]

    action_hints = ("교체", "조정", "확인", "점검", "청소", "수정", "리셋", "재부팅", "체결", "간격", "센서", "케이블", "위치", "파라미터")
    seen = {a for a in actions}
    for doc in docs:
        history = _clean(doc.get("점검이력"))
        if not history:
            continue
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


def _generate_alert_answer(*, line: str, equipment: str, error_name: str, occurred_at: str | None, docs: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    evidence = _build_evidence(docs, limit=12)
    prompt = f"""
당신은 반도체 후공정 설비 정비 지원 어시스턴트입니다.
아래 현재 발생 에러와 과거 인폼노트 이력만 근거로 정비 전 가이드를 작성하세요.
문서에 없는 조치는 새로 만들어내지 말고, 근거가 약하면 '추가 확인 필요'라고 표현하세요.

[현재 발생 에러]
- 발생 시각: {occurred_at or '-'}
- 라인: {line}
- 설비명: {equipment}
- 에러명: {error_name}

[검색 요약]
- 검색 이력 수: {stats.get('doc_count', 0)}건
- 동일 라인+동일 설비 이력: {stats.get('same_line_equipment_count', 0)}건
- 동일 설비 이력: {stats.get('same_equipment_count', 0)}건
- 에러명 직접/부분 유사 이력: {stats.get('error_like_count', 0)}건
- 최신 참고 이력 날짜: {stats.get('latest_date') or '-'}

[과거 인폼노트 이력]
{evidence}

반드시 아래 형식으로 답하세요.

## 요약
- 현재 에러와 가장 관련 높은 과거 이력을 2~4문장으로 요약합니다.
- 동일 설비 또는 동일 모델 기준인지 명확히 표현합니다.

## 우선 점검 항목
1. 현장에서 먼저 확인할 항목을 3~6개 작성합니다.
2. 과거 점검이력에 등장한 조치/확인사항을 우선 사용합니다.

## 과거 이력 패턴
- 반복적으로 확인되는 증상, 조치, 변경점, 설정값, 부품을 3~6개 bullet로 정리합니다.
- 가능한 경우 [# 번호] 근거를 붙입니다.

## 추가 확인 및 주의사항
- 실제 조치 전 추가로 확인할 항목과 데이터 해석 시 주의할 점을 2~4개 작성합니다.
""".strip()
    try:
        return _clean(alert_llm.invoke([HumanMessage(content=prompt)]).content)
    except Exception as error:
        return (
            "## 요약\n"
            f"- AI 요약 생성 중 오류가 발생했습니다: {error}\n"
            "- 아래 참고 이력 표를 기준으로 현장 점검을 진행해 주세요.\n\n"
            "## 우선 점검 항목\n"
            "1. 동일 라인/설비의 최근 이력을 먼저 확인합니다.\n"
            "2. 에러명과 점검이력에 반복되는 센서, 위치, 케이블, 설정값을 확인합니다.\n\n"
            "## 과거 이력 패턴\n"
            "- LLM 요약 실패로 자동 패턴 정리는 생략되었습니다.\n\n"
            "## 추가 확인 및 주의사항\n"
            "- 실제 조치는 현장 설비 상태와 안전 절차를 우선해 판단하세요."
        )


def analyze_error_event(
    *,
    line: str,
    equipment: str,
    error_name: str,
    occurred_at: str | None = None,
    process: str = "MP",
    reference_doc_count: int = 5,
) -> dict[str, Any]:
    """설비 에러 이벤트를 alert 전용 정밀 검색 로직으로 분석합니다.

    chat_test 채팅창은 자유 질문을 다루지만, alert 이벤트는 라인/설비명/에러명이 구조화되어 있습니다.
    따라서 line/equipment는 SQL 컬럼 검색으로 강하게 고정하고,
    error_name은 keyword + embedding 보강으로 오타/축약 표현을 흡수합니다.
    """
    started = perf_counter()
    line = _clean(line)
    equipment = _clean(equipment)
    error_name = _clean(error_name)
    process = _clean(process or "MP") or "MP"
    reference_doc_count = max(1, min(int(reference_doc_count or 5), 30))

    docs = search_alert_precision_documents(
        line=line,
        equipment=equipment,
        error_name=error_name,
        process=process,
        top_k=max(reference_doc_count, 10),
    )

    # 메일/표는 최신 데이터가 상단에 오도록 최신순 정렬합니다.
    sorted_docs = sorted(docs[: max(reference_doc_count, 1)], key=_date_key, reverse=True)
    stats = _match_stats(sorted_docs, line, equipment, error_name)
    answer = _generate_alert_answer(
        line=line,
        equipment=equipment,
        error_name=error_name,
        occurred_at=occurred_at,
        docs=sorted_docs,
        stats=stats,
    )

    history_rows = [_history_row(doc, idx + 1) for idx, doc in enumerate(sorted_docs)]
    actions = _extract_action_lines(answer, sorted_docs, limit=6)
    confidence = _estimate_confidence(sorted_docs, line, equipment, error_name)
    elapsed_ms = round((perf_counter() - started) * 1000, 2)

    match_summary = {
        "same_line_equipment_count": stats.get("same_line_equipment_count", 0),
        "same_equipment_count": stats.get("same_equipment_count", 0),
        "error_like_count": stats.get("error_like_count", 0),
        "latest_date": stats.get("latest_date", ""),
        "doc_count": stats.get("doc_count", len(sorted_docs)),
    }

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
        "match_summary": match_summary,
        "elapsed_ms": elapsed_ms,
        "links": _make_links(line, equipment, error_name, occurred_at),
        "retrieval_debug": {
            "mode": "alert_precision_column_first",
            "strategy": [
                "line/equipment column match",
                "error_name keyword/fuzzy match",
                "error_name embedding backup",
                "latest-date reranking",
            ],
            "doc_count": len(sorted_docs),
            "history_ids": [row.get("id") for row in history_rows],
            "match_summary": match_summary,
        },
    }
