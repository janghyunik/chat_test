import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Generator, Optional, TypedDict

from dotenv import load_dotenv
from langchain.schema import HumanMessage
from langchain_community.chat_models.ollama import ChatOllama
from langchain_core.prompts import PromptTemplate

from app.services.legacy.logging_utils import log_chat_history
from app.services.legacy.pg_vector_utils import extract_query_keywords, hybrid_search_similar_documents

load_dotenv()

LLM_MODEL = os.getenv("LLM_MODEL")
DENSE_TOP_K_PER_QUERY = int(os.getenv("RAG_DENSE_TOP_K_PER_QUERY", "6"))
KEYWORD_TOP_K = int(os.getenv("RAG_KEYWORD_TOP_K", "10"))
FIRST_PASS_KEEP = int(os.getenv("RAG_FIRST_PASS_KEEP", "18"))
FINAL_CONTEXT_DOCS = int(os.getenv("RAG_FINAL_CONTEXT_DOCS", "6"))

llm = ChatOllama(model=LLM_MODEL, temperature=0, stream=False)
streaming_llm = ChatOllama(model=LLM_MODEL, temperature=0, stream=True)

ALLOWED_REFERENCE_DOC_COUNTS = {1, 3, 5, 10, 20, 30}


def _normalize_reference_doc_count(value: Any | None) -> int:
    try:
        count = int(value) if value is not None else FINAL_CONTEXT_DOCS
    except Exception:
        count = FINAL_CONTEXT_DOCS
    if count not in ALLOWED_REFERENCE_DOC_COUNTS:
        count = FINAL_CONTEXT_DOCS
    return count

FOLLOW_UP_HINTS = (
    "그럼", "그건", "그거", "이거", "이건", "그 에러", "그 설비", "그 라인",
    "그 경우", "그 내용", "그 조치", "그 원인", "추가로", "그 다음", "그 이후",
    "왜", "원인은", "조치는", "방법은", "해결은", "언제", "얼마나", "몇번", "반복",
    "해당 증상", "이 증상", "그 증상", "이런 증상", "같은 증상", "유사 증상",
)
ACTION_HINTS = (
    "조치", "대응", "해결", "방법", "확인", "점검", "교체", "조정", "복구", "수리", "체크",
)
CAUSE_HINTS = (
    "원인", "왜", "이유", "발생한 이유", "무슨 이유", "원인이 뭐", "가능성", "징후",
)
COMPARE_HINTS = (
    "비교", "차이", "어느", "더 자주", "반복", "추세", "최근", "빈도", "몇 번", "많이",
)
SUMMARY_HINTS = (
    "이력", "내용", "정리", "요약", "무슨 일", "사례", "알려", "조회", "설명",
)
EQUIP_HISTORY_HINTS = (
    "어떤 설비", "어느 설비", "설비 이력", "발생했던 설비", "설비들은", "설비 목록", "어느 호기",
)
RECENT_HINTS = ("최근", "최신", "요즘", "근래", "최근에")
ACTION_SENTENCE_HINTS = (
    "교체", "점검", "확인", "조정", "청소", "체결", "재부팅", "리셋", "보정",
    "측정", "교정", "복구", "정렬", "설정", "수정", "분리", "체크", "점퍼",
)
CAUSE_SENTENCE_HINTS = (
    "원인", "불량", "이상", "오염", "마모", "느슨", "접촉", "단선", "오정렬",
    "간섭", "과열", "미감지", "오차", "오동작", "손상", "변형", "편차",
)
QUESTION_STOPWORDS = {
    "이력", "알려줘", "알려", "주세요", "좀", "해당", "관련", "내용", "무엇", "뭐", "정리", "조회",
    "설비", "라인", "호기", "에러", "오류", "증상", "발생", "경우", "그럼", "이건", "그건", "추가",
    "원인", "조치", "방법", "비교", "추세", "최근", "반복",
}


class AgenticChatState(TypedDict):
    user_question: str
    process: str
    mode: str
    llm_prompt: str
    llm_response: str
    docs: list[dict[str, Any]]
    selected_doc: Optional[dict[str, Any]]
    conversation_memory: dict[str, Any]
    retrieval_debug: dict[str, Any]
    playbook: dict[str, Any]
    verifier_notes: dict[str, Any]


SUMMARY_PROMPT = PromptTemplate(
    template=(
        """
당신은 반도체 설비 인폼노트 분석 어시스턴트입니다.
모든 질문은 인폼노트 DB 근거 기반으로만 답변하세요.
일반 지식, 추측, 문서 밖의 내용을 만들어내지 마세요.

[사용자 질문]
{question}

[검색 초점]
{focus_slots}

[플레이북 요약]
{playbook}

[근거 문서]
{evidence}

답변 원칙:
1. 사용자가 라인명/설비명을 쓰지 않아도 에러명, 증상, 점검이력 키워드를 기준으로 관련 이력을 종합하세요.
2. 특정 라인/설비가 명확하지 않으면 "특정 라인/설비로 한정하지 않고 검색된 이력 기준"이라고 표현하세요.
3. 질문이 조치/원인/추세처럼 보여도 답변 포맷은 항상 아래 동일한 형식을 유지하세요.
4. 근거 문서의 날짜, 라인, 설비명, 에러명을 적극 활용하세요.
5. 문서 근거가 약한 내용은 단정하지 말고 "확인된 이력 기준", "추가 확인 필요"라고 표현하세요.

반드시 아래 형식으로 답하세요.

## 요약
- 질문과 가장 관련 높은 인폼노트 이력을 2~4문장으로 요약합니다.
- 라인/설비/에러명이 명확히 확인되면 함께 언급합니다.

## 주요 이력
- 관련 문서에서 반복적으로 나타나는 이력을 3~6개 bullet로 정리합니다.
- 각 bullet에는 가능하면 날짜, 라인, 설비명, 에러명 중 확인되는 정보를 포함합니다.
- 가능한 경우 [#번호] 근거를 붙입니다.

## 조치 및 점검 패턴
- 점검이력에서 확인되는 조치, 확인, 교체, 조정, 재발 방지 포인트를 3~6개로 정리합니다.
- 문서에 없는 조치는 새로 만들어내지 마세요.

## 참고할 점
- 데이터 해석 시 주의할 점, 라인/설비명이 누락된 질문에서의 한계, 추가 확인이 필요한 항목을 2~4개 작성합니다.
        """.strip()
    ),
    input_variables=["question", "conversation_memory", "focus_slots", "playbook", "evidence"],
)

CAUSE_PROMPT = PromptTemplate(
    template=(
        """
당신은 반도체 설비 인폼노트 분석 어시스턴트입니다.
반드시 제공된 근거 문서 안에서만 답변하세요. 근거가 약하면 단정하지 말고 '가능성', '추가 확인 필요'라고 표현하세요.

[사용자 질문]
{question}

[현재 대화 맥락]
{conversation_memory}

[핵심 초점]
{focus_slots}

[플레이북 요약]
{playbook}

[근거 문서]
{evidence}

질문 유형: 원인 분석형

반드시 아래 형식으로 답하세요.

## 핵심 답변
- 가장 가능성 높은 원인 또는 판단을 2~4문장으로 정리합니다.

## 판단 근거
- 문서에서 확인되는 근거를 bullet 3~5개로 정리합니다.
- 가능한 경우 [#번호] 근거를 붙입니다.

## 추가 확인
- 원인 확정을 위해 더 확인할 항목을 2~4개 작성합니다.
        """.strip()
    ),
    input_variables=["question", "conversation_memory", "focus_slots", "playbook", "evidence"],
)

ACTION_PROMPT = PromptTemplate(
    template=(
        """
당신은 반도체 설비 인폼노트 분석 어시스턴트입니다.
반드시 제공된 근거 문서 안에서만 답변하세요. 문서에 없는 조치를 만들어내지 마세요.

[사용자 질문]
{question}

[현재 대화 맥락]
{conversation_memory}

[핵심 초점]
{focus_slots}

[플레이북 요약]
{playbook}

[근거 문서]
{evidence}

질문 유형: 조치/대응형

반드시 아래 형식으로 답하세요.

## 권장 조치
1. 우선순위 순서로 3~5개 조치를 작성합니다.
2. 각 조치는 짧고 실행 가능하게 작성합니다.

## 조치 근거
- 왜 이런 조치를 권하는지 bullet 2~4개로 정리합니다.
- 가능한 경우 [#번호] 근거를 붙입니다.

## 주의사항
- 바로 단정하면 안 되는 점이나 추가로 확인할 점을 2~4개 적습니다.
        """.strip()
    ),
    input_variables=["question", "conversation_memory", "focus_slots", "playbook", "evidence"],
)

COMPARE_PROMPT = PromptTemplate(
    template=(
        """
당신은 반도체 설비 인폼노트 분석 어시스턴트입니다.
반드시 제공된 근거 문서 안에서만 답변하세요. 빈도나 추세를 문서 범위 밖으로 일반화하지 마세요.

[사용자 질문]
{question}

[현재 대화 맥락]
{conversation_memory}

[핵심 초점]
{focus_slots}

[플레이북 요약]
{playbook}

[근거 문서]
{evidence}

질문 유형: 비교/반복/추세형

반드시 아래 형식으로 답하세요.

## 비교 결과
- 질문에 대한 비교 또는 추세 판단을 2~4문장으로 정리합니다.

## 근거 정리
- 빈도, 반복 패턴, 최근 사례를 bullet 3~5개로 정리합니다.
- 가능한 경우 [#번호] 근거를 붙입니다.

## 해석 시 주의점
- 표본 한계나 추가 확인이 필요한 점을 2~4개 적습니다.
        """.strip()
    ),
    input_variables=["question", "conversation_memory", "focus_slots", "playbook", "evidence"],
)

FOLLOWUP_PROMPT = PromptTemplate(
    template=(
        """
당신은 반도체 설비 인폼노트 분석 어시스턴트입니다.
이 질문은 이전 대화의 맥락을 이어받은 후속 질문입니다.
반드시 제공된 근거 문서 안에서만, 자연스럽고 간결하게 답변하세요.

[사용자 질문]
{question}

[현재 대화 맥락]
{conversation_memory}

[핵심 초점]
{focus_slots}

[플레이북 요약]
{playbook}

[근거 문서]
{evidence}

질문 유형: 후속 질문형

반드시 아래 형식으로 답하세요.

## 답변
- 바로 이어서 대화하듯 2~5문장으로 핵심 답변을 합니다.

## 근거
- 짧은 bullet 2~4개로만 정리합니다.
- 가능한 경우 [#번호] 근거를 붙입니다.

## 추가 확인
- 필요할 때만 1~3개만 적습니다.
        """.strip()
    ),
    input_variables=["question", "conversation_memory", "focus_slots", "playbook", "evidence"],
)

EQUIP_HISTORY_PROMPT = PromptTemplate(
    template=(
        """
당신은 반도체 설비 인폼노트 분석 어시스턴트입니다.
질문은 '이 증상/에러가 어느 설비에서 발생했는가'에 가깝습니다.
반드시 제공된 근거 문서 안에서만 답변하세요.

[사용자 질문]
{question}

[현재 대화 맥락]
{conversation_memory}

[핵심 초점]
{focus_slots}

[플레이북 요약]
{playbook}

[근거 문서]
{evidence}

질문 유형: 설비 이력 탐색형

반드시 아래 형식으로 답하세요.

## 관련 설비
- 해당 증상/에러가 확인된 설비를 bullet 2~6개로 정리합니다.
- 가능하면 라인/날짜/반복 여부를 함께 적습니다.

## 판단 근거
- 어떤 점검 이력 때문에 위 설비들을 연관 설비로 판단했는지 bullet 2~5개로 정리합니다.
- 가능한 경우 [#번호] 근거를 붙입니다.

## 참고할 점
- 동일 설비인지, 유사 설비인지, 증상이 완전히 같은지 추가 확인이 필요한 점을 2~4개 적습니다.
        """.strip()
    ),
    input_variables=["question", "conversation_memory", "focus_slots", "playbook", "evidence"],
)


def _snip(text: str, n: int = 120) -> str:
    value = str(text or "").replace("\n", " ").replace("|", "¦").strip()
    return value if len(value) <= n else value[:n] + " …"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _tokenize(text: str) -> list[str]:
    return [tok for tok in re.findall(r"[0-9A-Za-z가-힣]+", str(text or "").lower()) if tok]


def _clean_llm_text(text: str) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", cleaned)
    cleaned = re.sub(r"\n```$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _ensure_sectioned_response(text: str, default_heading: str) -> str:
    cleaned = _clean_llm_text(text)
    if not cleaned:
        return f"## {default_heading}\n내용을 생성하지 못했습니다."
    if re.search(r"(?m)^##\s+", cleaned):
        return cleaned
    return f"## {default_heading}\n{cleaned}"


def _safe_date(doc: dict[str, Any]) -> Optional[datetime]:
    raw = str(doc.get("날짜", "")).strip()
    if not raw:
        return None
    for candidate in (raw, raw[:19], raw[:10]):
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(candidate, fmt)
            except Exception:
                continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _doc_key(doc: dict[str, Any]) -> tuple[Any, ...]:
    return (
        doc.get("날짜"),
        doc.get("라인"),
        doc.get("공정"),
        doc.get("설비명"),
        doc.get("에러명"),
        doc.get("점검이력"),
    )


def _dedup_preserve_order(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output: list[dict[str, Any]] = []
    for doc in docs:
        key = _doc_key(doc)
        if key in seen:
            continue
        seen.add(key)
        output.append(doc)
    return output


def _question_mentions(value: str, question: str) -> bool:
    field_norm = _norm(value)
    question_norm = _norm(question)
    return bool(field_norm and question_norm and field_norm in question_norm)


def _extract_line_tokens(question: str) -> list[str]:
    values = set()
    for match in re.findall(r"([0-9A-Za-z가-힣]+)\s*라인", str(question or "")):
        token = str(match).strip()
        if token:
            values.add(token)
            values.add(f"{token}라인")
    return list(values)


def _extract_hoigi_tokens(question: str) -> list[str]:
    values = set()
    for match in re.findall(r"([0-9A-Za-z가-힣]+)\s*호기", str(question or "")):
        token = str(match).strip()
        if token:
            values.add(token)
            values.add(f"{token}호기")
    return list(values)


def _extract_error_tokens(question: str) -> list[str]:
    tokens = []
    for token in _tokenize(question):
        if token in QUESTION_STOPWORDS:
            continue
        if any(s in token for s in ("에러", "오류", "알람", "fault", "alarm")):
            tokens.append(token)
            continue
        if token.isdigit() and len(token) >= 2:
            tokens.append(token)
    return list(dict.fromkeys(tokens))[:8]


def _extract_symptom_keywords(question: str) -> list[str]:
    output = []
    seen = set()
    for token in _tokenize(question):
        if token in QUESTION_STOPWORDS:
            continue
        if token.endswith("라인") or token.endswith("호기"):
            continue
        if token.isdigit() and len(token) <= 1:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        output.append(token)
    return output[:10]


def _extract_structured_hints(question: str, memory: dict[str, Any], *, is_follow_up: bool) -> dict[str, Any]:
    q = _norm(question)
    line_tokens = _extract_line_tokens(question)
    hoigi_tokens = _extract_hoigi_tokens(question)
    error_tokens = _extract_error_tokens(question)
    symptom_keywords = _extract_symptom_keywords(question)

    if is_follow_up and any(phrase in q for phrase in ("해당 증상", "이 증상", "그 증상", "이런 증상", "같은 증상", "유사 증상")):
        remembered = list(memory.get("last_symptom_keywords", []) or [])
        for token in remembered:
            if token not in symptom_keywords:
                symptom_keywords.append(token)
        symptom_keywords = symptom_keywords[:10]

    return {
        "line_tokens": line_tokens,
        "hoigi_tokens": hoigi_tokens,
        "error_tokens": error_tokens,
        "symptom_keywords": symptom_keywords,
        "recent_only": any(hint in q for hint in RECENT_HINTS),
        "wants_equipment_list": any(hint in q for hint in EQUIP_HISTORY_HINTS),
    }


def _has_explicit_new_context(question: str) -> bool:
    return bool(
        _extract_line_tokens(question)
        or _extract_hoigi_tokens(question)
        or _extract_error_tokens(question)
        or len(_extract_symptom_keywords(question)) >= 4
    )


def _is_short_question(question: str) -> bool:
    return len(_tokenize(question)) <= 4


def _looks_like_follow_up(question: str, memory: dict[str, Any]) -> bool:
    if not any(memory.get(key) for key in ("current_line", "current_equip", "current_error", "last_symptom_keywords")):
        return False
    q = _norm(question)
    if _has_explicit_new_context(question) and not any(hint in q for hint in ("그럼", "해당", "이 증상", "그 증상")):
        return False
    if any(hint in q for hint in FOLLOW_UP_HINTS):
        return True
    return _is_short_question(q)


def _infer_intent(question: str, is_follow_up: bool, hints: dict[str, Any]) -> str:
    q = _norm(question)
    if hints.get("wants_equipment_list"):
        return "equipment_history"
    if is_follow_up and (_is_short_question(question) or any(hint in q for hint in FOLLOW_UP_HINTS)):
        if any(hint in q for hint in ACTION_HINTS):
            return "action"
        if any(hint in q for hint in CAUSE_HINTS):
            return "cause"
        if any(hint in q for hint in COMPARE_HINTS):
            return "compare"
        return "followup"
    if any(hint in q for hint in ACTION_HINTS):
        return "action"
    if any(hint in q for hint in CAUSE_HINTS):
        return "cause"
    if any(hint in q for hint in COMPARE_HINTS):
        return "compare"
    if any(hint in q for hint in SUMMARY_HINTS):
        return "summary"
    return "summary"


def _build_memory(previous_state: dict[str, Any] | None, recent_messages: list[dict[str, Any]] | None, process: str) -> dict[str, Any]:
    memory = dict((previous_state or {}).get("conversation_memory", {}) or {})
    memory.setdefault("process", process)

    if recent_messages:
        recent_user_questions = [m.get("content", "") for m in recent_messages if m.get("role") == "user"][-5:]
        if recent_user_questions:
            memory["recent_questions"] = recent_user_questions
        recent_assistant_summaries = [
            _snip(m.get("content", ""), 140) for m in recent_messages if m.get("role") == "assistant"
        ][-2:]
        if recent_assistant_summaries:
            memory["recent_answers"] = recent_assistant_summaries
    return memory


def _format_memory(memory: dict[str, Any], *, is_follow_up: bool) -> str:
    if not is_follow_up:
        return "새 질문으로 처리 중"
    lines = [
        f"공정: {memory.get('process', '-')}",
        f"이전 라인: {memory.get('current_line', '-')}",
        f"이전 설비명: {memory.get('current_equip', '-')}",
        f"이전 에러명: {memory.get('current_error', '-')}",
        f"이전 참고 요약: {memory.get('last_reference_summary', '-')}",
        f"이전 증상 키워드: {', '.join(memory.get('last_symptom_keywords', []) or []) or '-'}",
        f"직전 질문: {', '.join(memory.get('recent_questions', [])[-2:]) or '-'}",
    ]
    return "\n".join(lines)


def _extract_history_sentences(history: str) -> list[str]:
    raw_parts = re.split(r"[\n•\-]+|(?<=[.!?])\s+|→|=>|/", str(history or ""))
    lines: list[str] = []
    for part in raw_parts:
        cleaned = part.strip(" \t\r\n-•0123456789.)")
        cleaned = re.sub(r"\s+", " ", cleaned)
        if len(cleaned) >= 4:
            lines.append(cleaned)
    return lines


def _extract_action_sentences(history: str) -> list[str]:
    sentences = _extract_history_sentences(history)
    actions = [s for s in sentences if any(keyword in s for keyword in ACTION_SENTENCE_HINTS)]
    return actions[:5] if actions else sentences[:3]


def _extract_cause_clues(history: str) -> list[str]:
    sentences = _extract_history_sentences(history)
    causes = [s for s in sentences if any(keyword in s for keyword in CAUSE_SENTENCE_HINTS)]
    return causes[:4]


def _normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    history = str(doc.get("점검이력", "") or "")
    actions = _extract_action_sentences(history)
    causes = _extract_cause_clues(history)
    keywords = extract_query_keywords(
        " ".join([
            str(doc.get("라인", "")),
            str(doc.get("설비명", "")),
            str(doc.get("에러명", "")),
            history,
        ]),
        limit=10,
    )
    return {
        **doc,
        "normalized_summary": _snip(history, 180),
        "action_sentences": actions,
        "cause_clues": causes,
        "keywords": keywords,
    }


def _build_query_variants(question: str, process: str, memory: dict[str, Any], *, is_follow_up: bool, hints: dict[str, Any]) -> list[str]:
    keyword_query = " ".join(extract_query_keywords(question, limit=8))
    variants: list[str] = [question]

    if keyword_query and keyword_query != question:
        variants.append(keyword_query)

    # 라인/설비명이 없는 질문은 embedding된 text(라인+설비+에러 조합)와 벡터 매칭이 약해질 수 있습니다.
    # 따라서 에러명/증상/점검이력 키워드만으로도 keyword retrieval이 강하게 동작하도록
    # error-focused query를 별도로 추가합니다.
    error_tokens = list(hints.get("error_tokens", []) or [])
    symptom_tokens = list(hints.get("symptom_keywords", []) or [])
    focused_tokens = list(dict.fromkeys([*error_tokens, *symptom_tokens]))[:10]
    if focused_tokens:
        variants.append(" ".join(focused_tokens))
        variants.append("에러명 " + " ".join(focused_tokens[:8]))
        variants.append("점검이력 " + " ".join(focused_tokens[:8]))

    explicit_combo = " ".join(
        part for part in [
            process,
            *hints.get("line_tokens", [])[:1],
            *hints.get("hoigi_tokens", [])[:1],
            *hints.get("error_tokens", [])[:2],
            *hints.get("symptom_keywords", [])[:5],
        ] if part
    )
    if explicit_combo and explicit_combo != question:
        variants.append(explicit_combo)

    if is_follow_up and any(memory.get(key) for key in ("current_line", "current_equip", "current_error")):
        contextual = " ".join(
            part
            for part in [
                process,
                memory.get("current_line", ""),
                memory.get("current_equip", ""),
                memory.get("current_error", ""),
                " ".join(hints.get("symptom_keywords", [])[:4]),
                question,
            ]
            if part
        )
        if contextual:
            variants.append(contextual)

    if hints.get("wants_equipment_list"):
        equip_variant = " ".join(
            part for part in [
                "설비명",
                " ".join(hints.get("symptom_keywords", [])[:5]),
                memory.get("last_reference_summary", "") if is_follow_up else "",
            ] if part
        )
        if equip_variant:
            variants.append(equip_variant)

    if is_follow_up and memory.get("last_reference_summary"):
        variants.append(
            " ".join(
                part for part in [
                    memory.get("current_equip", ""),
                    memory.get("current_error", ""),
                    memory.get("last_reference_summary", ""),
                    question,
                ] if part
            )
        )

    deduped: list[str] = []
    seen = set()
    for item in variants:
        cleaned = re.sub(r"\s+", " ", item or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped[:8]


def _normalize_similarity(similarity: float, min_sim: float, max_sim: float) -> float:
    if max_sim - min_sim < 1e-9:
        return 1.0
    return 1.0 - ((similarity - min_sim) / (max_sim - min_sim))


def _field_match_score(question: str, field_value: str, exact_weight: float, token_weight: float, cap: float) -> float:
    question_norm = _norm(question)
    field_norm = _norm(field_value)
    if not field_norm:
        return 0.0
    score = 0.0
    if field_norm in question_norm:
        score += exact_weight
    keywords = extract_query_keywords(question, limit=10)
    matched = sum(1 for token in keywords if token in field_norm)
    score += min(cap, matched * token_weight)
    return score


def _line_token_match_score(doc_line: str, hints: dict[str, Any]) -> float:
    line_norm = _norm(doc_line)
    if not line_norm:
        return 0.0
    score = 0.0
    for token in hints.get("line_tokens", []):
        token_norm = _norm(token)
        if token_norm and (token_norm == line_norm or token_norm in line_norm or line_norm in token_norm):
            score += 1.8
    return min(score, 2.2)


def _hoigi_match_score(doc_equip: str, hints: dict[str, Any]) -> float:
    equip_norm = _norm(doc_equip)
    if not equip_norm:
        return 0.0
    score = 0.0
    for token in hints.get("hoigi_tokens", []):
        token_norm = _norm(token)
        if token_norm and token_norm in equip_norm:
            score += 2.0
    return min(score, 2.5)


def _error_token_match_score(doc_error: str, hints: dict[str, Any]) -> float:
    error_norm = _norm(doc_error)
    if not error_norm:
        return 0.0
    score = 0.0
    for token in hints.get("error_tokens", []):
        token_norm = _norm(token)
        if token_norm and token_norm in error_norm:
            score += 2.0 if token.isdigit() else 1.2
    return min(score, 2.8)


def _symptom_match_score(doc: dict[str, Any], hints: dict[str, Any]) -> float:
    haystack = _norm(" ".join([
        str(doc.get("에러명", "")),
        str(doc.get("점검이력", "")),
        str(doc.get("text", "")),
    ]))
    if not haystack:
        return 0.0
    score = 0.0
    for token in hints.get("symptom_keywords", []):
        token_norm = _norm(token)
        if token_norm and token_norm in haystack:
            score += 0.55 if token.isdigit() else 0.35
    return min(score, 2.6)


def _keyword_coverage_score(doc: dict[str, Any], hints: dict[str, Any]) -> float:
    """
    라인/설비명이 없는 질문에서 에러명/증상 키워드가 실제 에러명·점검이력 컬럼에
    얼마나 많이 포함되는지 별도로 점수화합니다.
    embedding이 라인+설비+에러 조합으로 되어 있어도, 컬럼 기반 매칭으로 보완하기 위함입니다.
    """
    tokens = list(dict.fromkeys([
        *list(hints.get("error_tokens", []) or []),
        *list(hints.get("symptom_keywords", []) or []),
    ]))[:12]
    if not tokens:
        return 0.0

    error_text = _norm(str(doc.get("에러명", "")))
    history_text = _norm(str(doc.get("점검이력", "")))
    text_all = _norm(" ".join([
        str(doc.get("text", "")),
        str(doc.get("에러명", "")),
        str(doc.get("점검이력", "")),
    ]))

    matched = 0
    score = 0.0
    for token in tokens:
        token_norm = _norm(token)
        if not token_norm:
            continue
        if token_norm in error_text:
            matched += 1
            score += 1.15 if token_norm.isdigit() else 0.9
        elif token_norm in history_text:
            matched += 1
            score += 0.65 if token_norm.isdigit() else 0.45
        elif token_norm in text_all:
            matched += 1
            score += 0.3

    coverage = matched / max(len(tokens), 1)
    score += coverage * 1.6
    return min(score, 5.0)


def _rerank_candidates(
    question: str,
    candidates: list[dict[str, Any]],
    *,
    memory: dict[str, Any],
    is_follow_up: bool,
    focus_slots: dict[str, str] | None = None,
    hints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    focus_slots = focus_slots or {}
    hints = hints or {}
    similarities = [float(doc.get("similarity", 9999.0) or 9999.0) for doc in candidates if doc.get("similarity") is not None]
    min_sim = min(similarities) if similarities else 0.0
    max_sim = max(similarities) if similarities else 1.0
    keyword_scores = [float(doc.get("keyword_score", 0.0) or 0.0) for doc in candidates]
    max_keyword_score = max(keyword_scores) if keyword_scores else 1.0

    dates = [d for d in (_safe_date(doc) for doc in candidates) if d is not None]
    newest = max(dates) if dates else None
    oldest = min(dates) if dates else None
    date_span = max((newest - oldest).days, 1) if newest and oldest else 1

    reranked: list[dict[str, Any]] = []
    for doc in candidates:
        vector_score = 0.0
        if doc.get("similarity") is not None:
            vector_score = _normalize_similarity(float(doc.get("similarity", 9999.0) or 9999.0), min_sim, max_sim) * 3.4

        sparse_score = 0.0
        if max_keyword_score > 0:
            sparse_score = (float(doc.get("keyword_score", 0.0) or 0.0) / max_keyword_score) * 2.8

        retrieval_bonus = min(1.2, 0.4 * len(doc.get("retrieval_channels", []) or []))
        query_hit_bonus = min(1.0, 0.22 * len(doc.get("query_hits", []) or []))

        equip_score = _field_match_score(question, str(doc.get("설비명", "")), 2.6, 0.7, 1.8)
        error_score = _field_match_score(question, str(doc.get("에러명", "")), 3.2, 0.8, 2.5)
        line_score = _field_match_score(question, str(doc.get("라인", "")), 1.0, 0.5, 1.0)
        history_score = _field_match_score(question, str(doc.get("점검이력", "")), 0.0, 0.22, 1.6)

        keyword_coverage = _keyword_coverage_score(doc, hints)
        column_boost = (
            _line_token_match_score(str(doc.get("라인", "")), hints)
            + _hoigi_match_score(str(doc.get("설비명", "")), hints)
            + _error_token_match_score(str(doc.get("에러명", "")), hints)
            + _symptom_match_score(doc, hints)
            + keyword_coverage
        )

        memory_boost = 0.0
        if is_follow_up:
            for field_key, doc_key, weight in (("current_line", "라인", 0.8), ("current_equip", "설비명", 1.4), ("current_error", "에러명", 1.5)):
                mem_value = _norm(memory.get(field_key, ""))
                doc_value = _norm(doc.get(doc_key, ""))
                if mem_value and doc_value and mem_value == doc_value:
                    memory_boost += weight
            if memory.get("last_symptom_keywords"):
                memory_boost += min(1.2, 0.2 * len([k for k in memory.get("last_symptom_keywords", []) if _norm(k) in _norm(str(doc.get("점검이력", "")))]))

        focus_boost = 0.0
        for slot_key, doc_key, weight in (("line", "라인", 1.0), ("equip", "설비명", 1.8), ("error", "에러명", 2.0)):
            slot_value = _norm(focus_slots.get(slot_key, ""))
            doc_value = _norm(doc.get(doc_key, ""))
            if slot_value and doc_value and slot_value == doc_value:
                focus_boost += weight

        date_score = 0.0
        doc_date = _safe_date(doc)
        if newest and doc_date:
            recency = 1.0 - ((newest - doc_date).days / date_span)
            date_score = max(0.0, recency) * (1.2 if hints.get("recent_only") else 0.8)

        total = vector_score + sparse_score + retrieval_bonus + query_hit_bonus + equip_score + error_score + line_score + history_score + column_boost + memory_boost + focus_boost + date_score
        cloned = dict(doc)
        cloned["rerank"] = {
            "vector": round(vector_score, 3),
            "sparse": round(sparse_score, 3),
            "retrieval": round(retrieval_bonus + query_hit_bonus, 3),
            "equip": round(equip_score, 3),
            "error": round(error_score, 3),
            "line": round(line_score, 3),
            "history": round(history_score, 3),
            "column": round(column_boost, 3),
            "keyword_coverage": round(keyword_coverage, 3),
            "memory": round(memory_boost, 3),
            "focus": round(focus_boost, 3),
            "date": round(date_score, 3),
            "total": round(total, 3),
        }
        reranked.append(cloned)

    reranked.sort(key=lambda item: item["rerank"]["total"], reverse=True)
    return reranked


def _infer_focus_slots(
    question: str,
    candidates: list[dict[str, Any]],
    memory: dict[str, Any],
    *,
    is_follow_up: bool,
    hints: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    focus: dict[str, str] = {"line": "", "equip": "", "error": ""}
    sources: dict[str, str] = {"line": "", "equip": "", "error": ""}

    line_scores: defaultdict[str, float] = defaultdict(float)
    equip_scores: defaultdict[str, float] = defaultdict(float)
    error_scores: defaultdict[str, float] = defaultdict(float)

    for rank, doc in enumerate(candidates[:10], start=1):
        base_weight = max(0.8, 6.0 - rank) + float(doc.get("rerank", {}).get("total", 0.0) or 0.0)
        line_val = str(doc.get("라인", "") or "")
        equip_val = str(doc.get("설비명", "") or "")
        error_val = str(doc.get("에러명", "") or "")

        if line_val:
            boost = 0.0
            if _question_mentions(line_val, question):
                boost += 4.0
            if any(_norm(token) in _norm(line_val) or _norm(line_val) in _norm(token) for token in hints.get("line_tokens", [])):
                boost += 3.0
            line_scores[line_val] += base_weight + boost

        if equip_val:
            boost = 0.0
            if _question_mentions(equip_val, question):
                boost += 4.5
            if any(_norm(token) in _norm(equip_val) for token in hints.get("hoigi_tokens", [])):
                boost += 3.5
            equip_scores[equip_val] += base_weight + boost

        if error_val:
            boost = 0.0
            if _question_mentions(error_val, question):
                boost += 4.5
            if any(_norm(token) in _norm(error_val) for token in hints.get("error_tokens", [])):
                boost += 3.5
            error_scores[error_val] += base_weight + boost

    for key, scores, mem_key in (
        ("line", line_scores, "current_line"),
        ("equip", equip_scores, "current_equip"),
        ("error", error_scores, "current_error"),
    ):
        if scores:
            best_value, _best_score = max(scores.items(), key=lambda x: x[1])
            focus[key] = best_value
            sources[key] = "candidate"
            if _question_mentions(best_value, question):
                sources[key] = "question"
        if not focus[key] and is_follow_up and memory.get(mem_key):
            focus[key] = str(memory.get(mem_key) or "")
            sources[key] = "memory"

    return focus, sources


def _build_second_pass_queries(question: str, process: str, focus_slots: dict[str, str], *, is_follow_up: bool, hints: dict[str, Any]) -> list[str]:
    queries: list[str] = [question]
    combined = " ".join(
        part for part in [process, focus_slots.get("line", ""), focus_slots.get("equip", ""), focus_slots.get("error", ""), " ".join(hints.get("symptom_keywords", [])[:4]), question] if part
    )
    if combined and combined != question:
        queries.append(combined)

    if focus_slots.get("equip") or focus_slots.get("error"):
        queries.append(
            " ".join(part for part in [focus_slots.get("equip", ""), focus_slots.get("error", ""), " ".join(hints.get("symptom_keywords", [])[:4]), question] if part)
        )

    if hints.get("wants_equipment_list"):
        queries.append(
            " ".join(part for part in ["설비명", focus_slots.get("error", ""), " ".join(hints.get("symptom_keywords", [])[:5]), question] if part)
        )

    if is_follow_up and focus_slots.get("equip"):
        queries.append(" ".join(part for part in [focus_slots.get("equip", ""), question] if part))

    deduped: list[str] = []
    seen = set()
    for item in queries:
        cleaned = re.sub(r"\s+", " ", item or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped[:5]


def _build_playbook(docs: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_docs = [_normalize_doc(doc) for doc in docs]
    equip_counter = Counter(str(doc.get("설비명", "")).strip() for doc in normalized_docs if doc.get("설비명"))
    error_counter = Counter(str(doc.get("에러명", "")).strip() for doc in normalized_docs if doc.get("에러명"))
    line_counter = Counter(str(doc.get("라인", "")).strip() for doc in normalized_docs if doc.get("라인"))
    action_counter = Counter()
    cause_counter = Counter()

    for doc in normalized_docs:
        for action in doc.get("action_sentences", [])[:3]:
            action_counter[action] += 1
        for cause in doc.get("cause_clues", [])[:3]:
            cause_counter[cause] += 1

    recent_docs = sorted(normalized_docs, key=lambda item: str(item.get("날짜", "")), reverse=True)[:4]

    return {
        "representative_equip": equip_counter.most_common(1)[0][0] if equip_counter else "-",
        "representative_error": error_counter.most_common(1)[0][0] if error_counter else "-",
        "representative_line": line_counter.most_common(1)[0][0] if line_counter else "-",
        "equip_rank": equip_counter.most_common(5),
        "error_rank": error_counter.most_common(5),
        "line_rank": line_counter.most_common(5),
        "common_actions": action_counter.most_common(6),
        "common_causes": cause_counter.most_common(6),
        "recent_cases": [
            {
                "date": str(doc.get("날짜", ""))[:10],
                "line": doc.get("라인", "-"),
                "equip": doc.get("설비명", "-"),
                "error": doc.get("에러명", "-"),
                "summary": doc.get("normalized_summary", "-"),
            }
            for doc in recent_docs
        ],
        "normalized_docs": normalized_docs,
    }


def _format_playbook(playbook: dict[str, Any]) -> str:
    lines = [
        f"대표 설비명: {playbook.get('representative_equip', '-')}",
        f"대표 에러명: {playbook.get('representative_error', '-')}",
        f"대표 라인: {playbook.get('representative_line', '-')}",
        f"반복 조치 패턴: {', '.join(f'{text}({count})' for text, count in playbook.get('common_actions', [])[:4]) or '-'}",
        f"반복 원인/징후: {', '.join(f'{text}({count})' for text, count in playbook.get('common_causes', [])[:4]) or '-'}",
        f"반복 설비: {', '.join(f'{text}({count})' for text, count in playbook.get('equip_rank', [])[:4]) or '-'}",
    ]
    recent = playbook.get("recent_cases", [])
    if recent:
        lines.append("최근 사례:")
        for item in recent:
            lines.append(f"- {item['date']} | {item['line']} | {item['equip']} | {item['error']} | {_snip(item['summary'], 90)}")
    return "\n".join(lines)


def _build_evidence_block(docs: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for idx, doc in enumerate(docs, start=1):
        blocks.append(
            f"[#{idx}] 날짜: {str(doc.get('날짜', ''))[:10]}\n"
            f"     라인: {doc.get('라인', '')}\n"
            f"     설비명: {doc.get('설비명', '')}\n"
            f"     에러명: {doc.get('에러명', '')}\n"
            f"     점검이력: {doc.get('점검이력', '')}"
        )
    return "\n\n".join(blocks)


def _build_reference_table(docs: list[dict[str, Any]], *, heading: str) -> str:
    lines = [
        heading,
        "| No | 날짜 | 라인 | 설비명 | 에러명 | 점검 요약 |",
        "|---:|:-----|:----|:------|:------|:---------|",
    ]
    for idx, doc in enumerate(docs, start=1):
        lines.append(
            f"| {idx} | {str(doc.get('날짜', ''))[:10]} | {str(doc.get('라인', ''))[:18]} | {str(doc.get('설비명', ''))[:30]} | {str(doc.get('에러명', ''))[:36]} | {_snip(doc.get('점검이력', ''), 80)} |"
        )
    return "\n".join(lines)


def _build_metadata_block(primary_doc: dict[str, Any], process: str, related_count: int) -> str:
    return "\n".join([
        "## 기본 정보",
        f"공정: {process}",
        f"라인: {primary_doc.get('라인', '-')}",
        f"설비명: {primary_doc.get('설비명', '-')}",
        f"에러명: {primary_doc.get('에러명', '-')}",
        f"날짜: {str(primary_doc.get('날짜', '-'))[:10] or '-'}",
        f"관련 문서 수: {related_count}",
    ])


def _compact_reference_bullets(docs: list[dict[str, Any]], *, heading: str) -> str:
    lines = [heading]
    for idx, doc in enumerate(docs[:4], start=1):
        lines.append(
            f"- [#{idx}] {str(doc.get('날짜', ''))[:10]} | {doc.get('라인', '-')} | {doc.get('설비명', '-')} | {doc.get('에러명', '-')} | {_snip(doc.get('점검이력', ''), 70)}"
        )
    return "\n".join(lines)


def _finalize_inform_response(body: str, primary_doc: dict[str, Any], docs: list[dict[str, Any]], process: str, intent: str) -> str:
    heading_map = {
        "summary": "요약",
        "cause": "핵심 답변",
        "action": "권장 조치",
        "compare": "비교 결과",
        "followup": "답변",
        "equipment_history": "관련 설비",
    }
    sectioned = _ensure_sectioned_response(body, heading_map.get(intent, "답변"))
    metadata = _build_metadata_block(primary_doc, process, len(docs))
    if intent in {"summary", "compare", "equipment_history"}:
        references = _build_reference_table(docs, heading="## 관련 이력")
    else:
        references = _compact_reference_bullets(docs, heading="## 참고 사례")
    return f"{sectioned}\n\n{metadata}\n\n{references}".strip()


def _generate_answer(
    question: str,
    process: str,
    memory: dict[str, Any],
    focus_slots: dict[str, str],
    playbook: dict[str, Any],
    docs: list[dict[str, Any]],
    *,
    intent: str,
    is_follow_up: bool,
) -> tuple[str, str]:
    evidence_block = _build_evidence_block(docs)
    prompt_template = SUMMARY_PROMPT
    prompt = prompt_template.format(
        question=question,
        conversation_memory="후속 질문 분기는 사용하지 않음",
        focus_slots=json.dumps(focus_slots, ensure_ascii=False),
        playbook=_format_playbook(playbook),
        evidence=evidence_block,
    )
    response = llm.invoke([HumanMessage(content=prompt)]).content.strip()
    return prompt, response


def _update_conversation_memory(
    memory: dict[str, Any],
    selected_doc: dict[str, Any],
    question: str,
    playbook: dict[str, Any],
    *,
    intent: str,
    hints: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(memory)
    updated["process"] = selected_doc.get("공정") or memory.get("process")
    updated["current_line"] = selected_doc.get("라인") or memory.get("current_line")
    updated["current_equip"] = selected_doc.get("설비명") or memory.get("current_equip")
    updated["current_error"] = selected_doc.get("에러명") or memory.get("current_error")
    updated["last_reference_doc_id"] = selected_doc.get("id")
    updated["last_reference_summary"] = _snip(selected_doc.get("점검이력", ""), 120)
    updated["last_playbook_actions"] = [text for text, _ in playbook.get("common_actions", [])[:3]]
    updated["last_intent"] = intent

    symptom_keywords = list(dict.fromkeys(
        [*hints.get("symptom_keywords", []), *extract_query_keywords(str(selected_doc.get("점검이력", "")), limit=8)]
    ))[:10]
    updated["last_symptom_keywords"] = symptom_keywords

    questions = list(updated.get("recent_questions", []))
    questions.append(question)
    updated["recent_questions"] = questions[-5:]
    return updated


def _prepare_answer_context(
    question: str,
    process: str,
    *,
    previous_state: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reference_doc_count: int | None = None,
) -> tuple[AgenticChatState, dict[str, Any]]:
    # 사용자 요청에 따라 "후속 질문" 분기/포맷을 더 이상 사용하지 않습니다.
    # 모든 질문은 독립적인 인폼노트 DB 질문으로 처리하고, 답변 포맷도 하나로 고정합니다.
    # 단, 답변 후 대화 메모리는 저장해 운영/디버깅 데이터로는 계속 활용합니다.
    memory = {"process": process}
    final_context_docs = _normalize_reference_doc_count(reference_doc_count)
    is_follow_up = False
    hints = _extract_structured_hints(question, memory, is_follow_up=False)
    intent = "summary"

    state: AgenticChatState = {
        "user_question": question,
        "process": process,
        "mode": "inform",
        "llm_prompt": "",
        "llm_response": "",
        "docs": [],
        "selected_doc": None,
        "conversation_memory": memory,
        "retrieval_debug": {},
        "playbook": {},
        "verifier_notes": {"enabled": False},
    }

    query_variants = _build_query_variants(question, process, memory, is_follow_up=is_follow_up, hints=hints)
    adaptive_keyword_top_k = max(KEYWORD_TOP_K, final_context_docs * 3, 20)
    first_candidates = hybrid_search_similar_documents(
        query_variants=query_variants,
        process=process,
        dense_top_k_per_query=max(DENSE_TOP_K_PER_QUERY, min(10, final_context_docs)),
        keyword_top_k=adaptive_keyword_top_k,
    )
    first_candidates = _dedup_preserve_order(first_candidates)[: max(FIRST_PASS_KEEP, final_context_docs)]
    first_ranked = _rerank_candidates(
        question,
        first_candidates,
        memory=memory,
        is_follow_up=is_follow_up,
        focus_slots={},
        hints=hints,
    )

    if not first_ranked:
        state["llm_response"] = "관련 인폼노트 이력을 찾지 못했습니다. 설비명, 에러명, 라인, 증상, 날짜 조건을 조금 더 구체적으로 입력해 주세요."
        state["retrieval_debug"] = {
            "query_variants": query_variants,
            "second_queries": [],
            "focus_slots": {},
            "focus_sources": {},
            "is_follow_up": is_follow_up,
            "intent": intent,
            "hints": hints,
            "candidate_count_first": 0,
            "candidate_count_second": 0,
            "candidate_count_final": 0,
            "reference_doc_count": final_context_docs,
            "adaptive_keyword_top_k": locals().get("adaptive_keyword_top_k", KEYWORD_TOP_K),
        }
        return state, {
            "intent": intent,
            "is_follow_up": is_follow_up,
            "memory": memory,
            "playbook": {},
            "evidence_docs": [],
            "selected_doc": None,
            "focus_slots": {},
            "focus_sources": {},
            "hints": hints,
            "query_variants": query_variants,
            "second_queries": [],
        }

    focus_slots, focus_sources = _infer_focus_slots(question, first_ranked, memory, is_follow_up=is_follow_up, hints=hints)
    second_queries = _build_second_pass_queries(question, process, focus_slots, is_follow_up=is_follow_up, hints=hints)

    # 사용자가 명시한 라인/설비만 DB filter로 사용합니다.
    # 후보에서 추정된 설비를 바로 filter로 걸면, 라인/설비명이 없는 에러명 질문에서
    # 첫 후보에 과도하게 끌려가 정확도가 떨어질 수 있습니다.
    line_filter = focus_slots.get("line", "") if focus_sources.get("line") == "question" else ""
    equip_filter = focus_slots.get("equip", "") if focus_sources.get("equip") == "question" else ""

    second_candidates = hybrid_search_similar_documents(
        query_variants=second_queries,
        process=process,
        dense_top_k_per_query=max(DENSE_TOP_K_PER_QUERY, min(10, final_context_docs)),
        keyword_top_k=adaptive_keyword_top_k,
        line=line_filter,
        equip=equip_filter,
    )

    merged_candidates = _dedup_preserve_order(first_ranked + second_candidates)
    final_ranked = _rerank_candidates(
        question,
        merged_candidates,
        memory=memory,
        is_follow_up=is_follow_up,
        focus_slots=focus_slots,
        hints=hints,
    )

    if not final_ranked:
        state["llm_response"] = "관련 인폼노트 이력을 찾지 못했습니다. 설비명, 에러명, 라인, 증상, 날짜 조건을 조금 더 구체적으로 입력해 주세요."
        state["retrieval_debug"] = {
            "query_variants": query_variants,
            "second_queries": second_queries,
            "focus_slots": focus_slots,
            "focus_sources": focus_sources,
            "is_follow_up": is_follow_up,
            "intent": intent,
            "hints": hints,
            "candidate_count_first": len(first_candidates),
            "candidate_count_second": len(second_candidates),
            "candidate_count_final": 0,
            "reference_doc_count": final_context_docs,
            "adaptive_keyword_top_k": locals().get("adaptive_keyword_top_k", KEYWORD_TOP_K),
        }
        return state, {
            "intent": intent,
            "is_follow_up": is_follow_up,
            "memory": memory,
            "playbook": {},
            "evidence_docs": [],
            "selected_doc": None,
            "focus_slots": focus_slots,
            "focus_sources": focus_sources,
            "hints": hints,
            "query_variants": query_variants,
            "second_queries": second_queries,
        }

    evidence_docs = final_ranked[:final_context_docs]
    selected_doc = evidence_docs[0]
    playbook = _build_playbook(evidence_docs)

    state["docs"] = evidence_docs
    state["selected_doc"] = selected_doc
    state["playbook"] = {key: value for key, value in playbook.items() if key != "normalized_docs"}
    state["retrieval_debug"] = {
        "query_variants": query_variants,
        "second_queries": second_queries,
        "focus_slots": focus_slots,
        "focus_sources": focus_sources,
        "is_follow_up": is_follow_up,
        "intent": intent,
        "hints": hints,
        "candidate_count_first": len(first_candidates),
        "candidate_count_second": len(second_candidates),
        "candidate_count_final": len(final_ranked),
        "reference_doc_count": final_context_docs,
        "document_count": len(evidence_docs),
    }

    return state, {
        "intent": intent,
        "is_follow_up": is_follow_up,
        "memory": memory,
        "playbook": playbook,
        "evidence_docs": evidence_docs,
        "selected_doc": selected_doc,
        "focus_slots": focus_slots,
        "focus_sources": focus_sources,
        "hints": hints,
        "query_variants": query_variants,
        "second_queries": second_queries,
    }



def answer_question_stream(
    question: str,
    process: str,
    *,
    previous_state: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reference_doc_count: int | None = None,
) -> Generator[str, None, AgenticChatState]:
    state, context = _prepare_answer_context(
        question,
        process,
        previous_state=previous_state,
        recent_messages=recent_messages,
        reference_doc_count=reference_doc_count,
    )

    if not state.get("selected_doc") or not context.get("evidence_docs"):
        fallback = state.get("llm_response") or "관련 인폼노트 이력을 찾지 못했습니다."
        state["llm_prompt"] = ""
        state["llm_response"] = fallback
        try:
            log_chat_history(question, fallback, [])
        except Exception as error:
            print(f"[WARN] log save failed: {error}")
        yield fallback
        return state

    try:
        prompt_template = SUMMARY_PROMPT
        prompt = prompt_template.format(
            question=question,
            conversation_memory="후속 질문 분기는 사용하지 않음",
            focus_slots=json.dumps(context["focus_slots"], ensure_ascii=False),
            playbook=_format_playbook(context["playbook"]),
            evidence=_build_evidence_block(context["evidence_docs"]),
        )
        state["llm_prompt"] = prompt
    except Exception as error:
        prompt = ""
        state["llm_prompt"] = ""
        draft = f"## 답변\n질문에 대한 답변 생성 중 오류가 발생했습니다.\n\n## 추가 확인\n- LLM 프롬프트 구성 오류: {error}"
        final_text = _finalize_inform_response(draft, context["selected_doc"], context["evidence_docs"], process, context["intent"] )
        updated_memory = _update_conversation_memory(
            context["memory"],
            context["selected_doc"],
            question,
            context["playbook"],
            intent=context["intent"],
            hints=context["hints"],
        )
        state["conversation_memory"] = updated_memory
        state["llm_response"] = final_text
        yield final_text
        return state

    chunks: list[str] = []
    try:
        for chunk in streaming_llm.stream([HumanMessage(content=prompt)]):
            content = getattr(chunk, "content", None)
            if content is None:
                content = str(chunk)
            if not content:
                continue
            chunks.append(content)
            yield content
    except Exception as error:
        fallback = f"## 답변\n질문에 대한 답변 생성 중 오류가 발생했습니다.\n\n## 추가 확인\n- LLM 스트리밍 오류: {error}"
        final_text = _finalize_inform_response(fallback, context["selected_doc"], context["evidence_docs"], process, context["intent"] )
        updated_memory = _update_conversation_memory(
            context["memory"],
            context["selected_doc"],
            question,
            context["playbook"],
            intent=context["intent"],
            hints=context["hints"],
        )
        state["conversation_memory"] = updated_memory
        state["llm_response"] = final_text
        yield final_text
        return state

    draft = "".join(chunks).strip()
    if not draft:
        draft = "## 답변\n질문에 대한 답변을 생성하지 못했습니다.\n\n## 추가 확인\n- 잠시 후 다시 시도해 주세요."

    final_text = _finalize_inform_response(draft, context["selected_doc"], context["evidence_docs"], process, context["intent"] )
    if final_text.startswith(draft):
        tail = final_text[len(draft):]
    else:
        tail = f"\n\n{_build_metadata_block(context['selected_doc'], process, len(context['evidence_docs']))}"
        if context["intent"] in {"summary", "compare", "equipment_history"}:
            tail += f"\n\n{_build_reference_table(context['evidence_docs'], heading='## 관련 이력')}"
        else:
            tail += f"\n\n{_compact_reference_bullets(context['evidence_docs'], heading='## 참고 사례')}"
    if tail:
        yield tail

    updated_memory = _update_conversation_memory(
        context["memory"],
        context["selected_doc"],
        question,
        context["playbook"],
        intent=context["intent"],
        hints=context["hints"],
    )

    state["conversation_memory"] = updated_memory
    state["llm_response"] = final_text

    try:
        log_chat_history(question, final_text, context["evidence_docs"] )
    except Exception as error:
        print(f"[WARN] log save failed: {error}")

    return state


def answer_question_direct(
    question: str,
    process: str,
    *,
    previous_state: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reference_doc_count: int | None = None,
) -> AgenticChatState:
    state, context = _prepare_answer_context(
        question,
        process,
        previous_state=previous_state,
        recent_messages=recent_messages,
        reference_doc_count=reference_doc_count,
    )

    if not state.get("selected_doc") or not context.get("evidence_docs"):
        return state

    try:
        prompt, draft = _generate_answer(
            question,
            process,
            context["memory"],
            context["focus_slots"],
            context["playbook"],
            context["evidence_docs"],
            intent=context["intent"],
            is_follow_up=context["is_follow_up"],
        )
    except Exception as error:
        prompt = ""
        draft = f"## 답변\n질문에 대한 답변 생성 중 오류가 발생했습니다.\n\n## 추가 확인\n- LLM 호출 오류: {error}"

    final_text = _finalize_inform_response(draft, context["selected_doc"], context["evidence_docs"], process, context["intent"])
    updated_memory = _update_conversation_memory(
        context["memory"],
        context["selected_doc"],
        question,
        context["playbook"],
        intent=context["intent"],
        hints=context["hints"],
    )

    state["llm_prompt"] = prompt
    state["llm_response"] = final_text
    state["conversation_memory"] = updated_memory

    try:
        log_chat_history(question, final_text, context["evidence_docs"])
    except Exception as error:
        print(f"[WARN] log save failed: {error}")

    return state
