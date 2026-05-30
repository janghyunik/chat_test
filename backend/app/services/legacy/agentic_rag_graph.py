import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Generator, Optional, TypedDict, Iterable

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
MAX_QUERY_VARIANTS = int(os.getenv("RAG_MAX_QUERY_VARIANTS", "2"))

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


def _compact_norm(text: Any) -> str:
    """라인/설비명/에러명 비교를 위한 강한 정규화입니다.

    공백, 하이픈, 괄호, 특수기호를 제거해서 다음 표현을 같은 축에서 비교합니다.
    - ATPS-1L02 == ATPS 1L02 == atps1l02
    - 1L / 1라인 / 1line -> line token 추출 단계에서 1로 확장
    - (X0306) == X0306
    """
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


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
    field_norm = _compact_norm(value)
    question_norm = _compact_norm(question)
    return bool(field_norm and question_norm and field_norm in question_norm)


def _line_aliases(token: str) -> list[str]:
    raw = str(token or "").strip()
    compact = _compact_norm(raw)
    if not compact:
        return []
    aliases = [raw, compact]
    m = re.fullmatch(r"([0-9]+)(?:l|line|라인)?", compact)
    if m:
        num = m.group(1)
        aliases.extend([num, f"{num}L", f"{num}l", f"{num}라인", f"{num}line"])
    m2 = re.fullmatch(r"([a-z]+[0-9]+)", compact)
    if m2:
        val = m2.group(1).upper()
        aliases.extend([val, f"{val}라인", f"{val}line"])
    # 순서 유지 중복 제거
    out, seen = [], set()
    for item in aliases:
        key = _compact_norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(str(item))
    return out[:12]


def _extract_equipment_parts(question: str) -> dict[str, list[str]]:
    raw = str(question or "")
    upper = raw.upper()
    full_terms: list[str] = []
    model_terms: list[str] = []
    suffix_terms: list[str] = []
    unit_terms: list[str] = []
    line_terms: list[str] = []
    common_non_models = {
        "ERR", "ERROR", "ALARM", "FAIL", "CHECK", "PICKER", "CHANGE", "SETUP", "TRAY",
        "LOADER", "INLET", "REEL", "PKG", "JOB", "SEND", "WAIT", "POS", "BLOCK", "PRINT",
    }

    pattern = re.compile(r"\b([A-Z][A-Z0-9]{1,12})\s*[-_ ]\s*([0-9]+L?[0-9]*[A-Z]?|[0-9]+[A-Z]?)\s*(?:호기)?\b", re.IGNORECASE)
    for m in pattern.finditer(upper):
        model = m.group(1).upper()
        suffix = m.group(2).upper()
        if model in common_non_models or re.fullmatch(r"X[0-9]{3,}", model):
            continue
        full_terms.extend([f"{model}-{suffix}", f"{model}{suffix}", f"{model} {suffix}"])
        model_terms.append(model)
        suffix_terms.append(suffix)
        unit_terms.append(suffix)
        lm = re.match(r"([0-9]+)L(.+)", suffix, flags=re.IGNORECASE)
        if lm:
            line_terms.extend(_line_aliases(lm.group(1)))
            if lm.group(2):
                unit_terms.append(lm.group(2).upper())

    pattern2 = re.compile(r"\b([A-Z][A-Z0-9]{1,12})\s*([0-9]+[A-Z]?)\s*호기\b", re.IGNORECASE)
    for m in pattern2.finditer(upper):
        model = m.group(1).upper()
        suffix = m.group(2).upper()
        if model in common_non_models or re.fullmatch(r"X[0-9]{3,}", model):
            continue
        full_terms.extend([f"{model}-{suffix}", f"{model}{suffix}"])
        model_terms.append(model)
        suffix_terms.append(suffix)
        unit_terms.append(suffix)

    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,12}\b", upper):
        if token in common_non_models:
            continue
        if re.fullmatch(r"X[0-9]{3,}", token):
            continue
        model_terms.append(token)

    def uniq(values: list[str], limit: int = 10) -> list[str]:
        out, seen = [], set()
        for v in values:
            key = _compact_norm(v)
            if key and key not in seen:
                seen.add(key)
                out.append(v)
            if len(out) >= limit:
                break
        return out

    return {
        "full": uniq(full_terms, 12),
        "model": uniq(model_terms, 10),
        "suffix": uniq(suffix_terms, 10),
        "unit": uniq(unit_terms, 12),
        "line_from_equip": uniq(line_terms, 10),
    }


def _extract_line_tokens(question: str) -> list[str]:
    values: list[str] = []
    raw = str(question or "")
    for match in re.findall(r"([0-9]+|[A-Za-z][0-9]+)\s*(?:라인|line)\b", raw, flags=re.IGNORECASE):
        values.extend(_line_aliases(match))
    for match in re.findall(r"\b([0-9]+)\s*[Ll]\b", raw):
        values.extend(_line_aliases(match))
    for match in re.findall(r"\b([Cc][0-9]+)\b", raw):
        values.extend(_line_aliases(match))
    values.extend(_extract_equipment_parts(question).get("line_from_equip", []))

    out, seen = [], set()
    for item in values:
        key = _compact_norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(str(item))
    return out[:16]


def _extract_hoigi_tokens(question: str) -> list[str]:
    values: list[str] = []
    for match in re.findall(r"([0-9A-Za-z]+)\s*호기", str(question or "")):
        token = str(match).strip()
        if token:
            values.extend([token, f"{token}호기"])
    parts = _extract_equipment_parts(question)
    values.extend(parts.get("full", []))
    values.extend(parts.get("model", []))
    values.extend(parts.get("unit", []))
    out, seen = [], set()
    for item in values:
        key = _compact_norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(str(item))
    return out[:20]


def _build_phrase_terms(question: str, *, min_n: int = 2, max_n: int = 4, limit: int = 16) -> list[str]:
    tokens = [t for t in _tokenize(question) if t not in QUESTION_STOPWORDS]
    grams: list[str] = []
    for n in range(min_n, max_n + 1):
        for i in range(0, max(0, len(tokens) - n + 1)):
            gram = " ".join(tokens[i:i+n])
            if len(_compact_norm(gram)) >= 3:
                grams.append(gram)
    out, seen = [], set()
    for item in grams:
        key = _compact_norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _extract_error_tokens(question: str) -> list[str]:
    tokens: list[str] = []
    raw = str(question or "")
    # X0306, X0119 같은 에러/센서 코드는 반드시 보존합니다.
    tokens.extend(re.findall(r"\b[A-Za-z]+[0-9]{2,}[A-Za-z0-9]*\b", raw, flags=re.IGNORECASE))
    # 582 같은 숫자 에러 코드도 보존합니다.
    tokens.extend(re.findall(r"\b\d{2,}\b", raw))
    # 실제 에러명은 phrase가 중요합니다. 예: 부자재 공급 에러, 라벨 PICKER 진공 ERROR
    tokens.extend(_build_phrase_terms(raw, min_n=2, max_n=4, limit=18))
    for token in _tokenize(raw):
        if token in QUESTION_STOPWORDS:
            continue
        if token.isdigit() and len(token) < 2:
            continue
        if any(s in token for s in ("err", "error", "fail", "alarm", "에러", "오류", "알람")):
            tokens.append(token)
            continue
        if len(token) >= 2:
            tokens.append(token)
    out, seen = [], set()
    for item in tokens:
        key = _compact_norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(str(item))
        if len(out) >= 30:
            break
    return out


def _extract_symptom_keywords(question: str) -> list[str]:
    output: list[str] = []
    seen = set()
    for phrase in _build_phrase_terms(question, min_n=2, max_n=3, limit=12):
        key = _compact_norm(phrase)
        if key and key not in seen:
            seen.add(key)
            output.append(phrase)
    for token in _tokenize(question):
        if token in QUESTION_STOPWORDS:
            continue
        if token.endswith("라인") or token.endswith("호기"):
            continue
        if token.isdigit() and len(token) <= 1:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        key = _compact_norm(token)
        if key in seen:
            continue
        seen.add(key)
        output.append(token)
    return output[:24]


def _extract_structured_hints(question: str, memory: dict[str, Any], *, is_follow_up: bool) -> dict[str, Any]:
    q = _norm(question)
    equip_parts = _extract_equipment_parts(question)
    line_tokens = _extract_line_tokens(question)
    hoigi_tokens = _extract_hoigi_tokens(question)
    error_tokens = _extract_error_tokens(question)
    symptom_keywords = _extract_symptom_keywords(question)

    if is_follow_up and any(phrase in q for phrase in ("해당 증상", "이 증상", "그 증상", "이런 증상", "같은 증상", "유사 증상")):
        remembered = list(memory.get("last_symptom_keywords", []) or [])
        for token in remembered:
            if token not in symptom_keywords:
                symptom_keywords.append(token)
        symptom_keywords = symptom_keywords[:16]

    return {
        "line_tokens": line_tokens,
        "hoigi_tokens": hoigi_tokens,
        "equip_tokens": _unique_for_agent([*equip_parts.get("full", []), *equip_parts.get("model", []), *equip_parts.get("unit", [])], limit=20),
        "equip_full_terms": equip_parts.get("full", []),
        "equip_model_terms": equip_parts.get("model", []),
        "equip_unit_terms": equip_parts.get("unit", []),
        "error_tokens": error_tokens,
        "error_terms": error_tokens,
        "symptom_keywords": symptom_keywords,
        "phrase_terms": _build_phrase_terms(question, min_n=2, max_n=4, limit=20),
        "recent_only": any(hint in q for hint in RECENT_HINTS),
        "wants_equipment_list": any(hint in q for hint in EQUIP_HISTORY_HINTS),
    }


def _unique_for_agent(values: Iterable[Any], *, limit: int = 10) -> list[str]:
    out, seen = [], set()
    for value in values:
        text = str(value or "").strip()
        key = _compact_norm(text)
        if key and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


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
    """검색 질의를 최대 2개로 제한해 1차 후보 검색 시간을 줄입니다.

    기존 버전은 설비/라인/에러/증상 조합별 질의를 많이 만들어 dense/vector 검색과
    keyword 검색을 여러 번 수행했습니다. 정합성은 보완되지만, Ollama embedding 호출과
    PostgreSQL ILIKE 검색이 질의 개수만큼 반복되어 1차 후보 검색 시간이 길어졌습니다.

    Step26 정책:
    1) 원 질문은 항상 유지합니다.
    2) 두 번째 질의는 라인/설비/에러/증상 핵심 슬롯을 압축한 compact query 하나만 사용합니다.
    3) 컬럼 우선 검색은 pg_vector_utils에서 최대 2개 질의를 합친 combined query로 한 번 수행합니다.
    """
    max_variants = max(1, min(MAX_QUERY_VARIANTS, 2))
    original = re.sub(r"\s+", " ", question or "").strip()

    equip_full = list(hints.get("equip_full_terms", []) or [])
    equip_model = list(hints.get("equip_model_terms", []) or [])
    equip_unit = list(hints.get("equip_unit_terms", []) or [])
    line_tokens = list(hints.get("line_tokens", []) or [])
    error_tokens = list(hints.get("error_tokens", []) or [])
    symptom_tokens = list(hints.get("symptom_keywords", []) or [])
    phrase_terms = list(hints.get("phrase_terms", []) or [])
    keyword_tokens = extract_query_keywords(question, limit=12)

    # 핵심 슬롯 우선순위:
    # - 설비 full term이 있으면 full 설비명 + 에러/phrase
    # - full 설비명이 없으면 모델/호기 + 라인 + 에러/phrase
    # - 설비 정보가 없으면 에러명/phrase/증상/코드 중심
    focused_tokens: list[str] = []
    if equip_full:
        focused_tokens.extend(equip_full[:2])
        focused_tokens.extend(error_tokens[:5])
        focused_tokens.extend(phrase_terms[:4])
        focused_tokens.extend(symptom_tokens[:4])
    elif equip_model:
        focused_tokens.extend(equip_model[:2])
        focused_tokens.extend(equip_unit[:2])
        focused_tokens.extend(line_tokens[:2])
        focused_tokens.extend(error_tokens[:5])
        focused_tokens.extend(phrase_terms[:4])
        focused_tokens.extend(symptom_tokens[:4])
    else:
        focused_tokens.extend(line_tokens[:2])
        focused_tokens.extend(error_tokens[:6])
        focused_tokens.extend(phrase_terms[:5])
        focused_tokens.extend(symptom_tokens[:5])
        focused_tokens.extend(keyword_tokens[:8])

    if hints.get("wants_equipment_list"):
        focused_tokens.insert(0, "설비명")

    compact_query = " ".join(_unique_for_agent(focused_tokens, limit=14)).strip()
    keyword_query = " ".join(keyword_tokens[:10]).strip()

    candidates = [original]
    # compact_query가 원문과 거의 같지 않을 때만 두 번째 검색 질의로 사용합니다.
    if compact_query and _compact_norm(compact_query) != _compact_norm(original):
        candidates.append(compact_query)
    elif keyword_query and _compact_norm(keyword_query) != _compact_norm(original):
        candidates.append(keyword_query)

    deduped: list[str] = []
    seen = set()
    for item in candidates:
        cleaned = re.sub(r"\s+", " ", item or "").strip()
        key = _compact_norm(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= max_variants:
            break
    return deduped

def _normalize_similarity(similarity: float, min_sim: float, max_sim: float) -> float:
    if max_sim - min_sim < 1e-9:
        return 1.0
    return 1.0 - ((similarity - min_sim) / (max_sim - min_sim))


def _field_match_score(question: str, field_value: str, exact_weight: float, token_weight: float, cap: float) -> float:
    question_norm = _compact_norm(question)
    field_norm = _compact_norm(field_value)
    if not field_norm:
        return 0.0
    score = 0.0
    if field_norm and field_norm in question_norm:
        score += exact_weight
    keywords = extract_query_keywords(question, limit=14)
    matched = sum(1 for token in keywords if _compact_norm(token) and _compact_norm(token) in field_norm)
    score += min(cap, matched * token_weight)
    return score


def _line_token_match_score(doc_line: str, hints: dict[str, Any], doc_equip: str = "") -> float:
    line_norm = _compact_norm(doc_line)
    equip_norm = _compact_norm(doc_equip)
    if not line_norm and not equip_norm:
        return 0.0
    score = 0.0
    for token in hints.get("line_tokens", []):
        token_norm = _compact_norm(token)
        if not token_norm:
            continue
        aliases = [_compact_norm(a) for a in _line_aliases(token)]
        if line_norm and any(a and (a == line_norm or a in line_norm or line_norm in a) for a in aliases):
            score += 7.0
        # 설비명에 2L, 4L 같은 라인 코드가 들어있는 경우 보조 점수
        if equip_norm and any(a and a in equip_norm for a in aliases):
            score += 2.5
    return min(score, 9.0)


def _equipment_match_score(doc_equip: str, hints: dict[str, Any]) -> float:
    equip_norm = _compact_norm(doc_equip)
    if not equip_norm:
        return 0.0
    score = 0.0
    full_terms = [_compact_norm(t) for t in hints.get("equip_full_terms", []) if _compact_norm(t)]
    model_terms = [_compact_norm(t) for t in hints.get("equip_model_terms", []) if _compact_norm(t)]
    unit_terms = [_compact_norm(t) for t in hints.get("equip_unit_terms", []) if _compact_norm(t)]

    for term in full_terms:
        if term and term in equip_norm:
            score += 18.0
    # 모델+호기가 같이 맞으면 full term 다음으로 강하게 반영합니다.
    for model in model_terms:
        if model and model in equip_norm:
            score += 4.0
            if any(unit and unit in equip_norm for unit in unit_terms):
                score += 8.0
    # 호기 단독은 오탐 위험이 높아서 낮게만 반영합니다.
    if not model_terms:
        for unit in unit_terms:
            if len(unit) >= 2 and unit in equip_norm:
                score += 2.0
    return min(score, 24.0)


def _hoigi_match_score(doc_equip: str, hints: dict[str, Any]) -> float:
    # 기존 함수명 호환용. 실제로는 equipment full/model/unit scoring을 사용합니다.
    return _equipment_match_score(doc_equip, hints)


def _error_token_match_score(doc_error: str, hints: dict[str, Any], doc_history: str = "", doc_text: str = "") -> float:
    error_norm = _compact_norm(doc_error)
    history_norm = _compact_norm(doc_history)
    text_norm = _compact_norm(doc_text)
    combined = " ".join([error_norm, history_norm, text_norm])
    if not combined.strip():
        return 0.0
    score = 0.0
    error_terms = list(hints.get("error_terms", []) or hints.get("error_tokens", []) or [])
    phrase_terms = list(hints.get("phrase_terms", []) or [])
    codes = [t for t in error_terms if re.fullmatch(r"x?\d{2,}[a-z0-9]*", _compact_norm(t)) or _compact_norm(t).isdigit()]

    for token in error_terms[:30]:
        token_norm = _compact_norm(token)
        if not token_norm:
            continue
        is_code = token_norm in [_compact_norm(c) for c in codes]
        is_phrase = len(token_norm) >= 5 or " " in str(token)
        if token_norm in error_norm:
            score += 16.0 if is_code else (10.0 if is_phrase else 5.0)
        elif token_norm in history_norm:
            score += 8.0 if is_code else (5.0 if is_phrase else 2.5)
        elif token_norm in text_norm:
            score += 5.0 if is_code else 1.5

    for phrase in phrase_terms[:16]:
        pn = _compact_norm(phrase)
        if not pn:
            continue
        if pn in error_norm:
            score += 8.0
        elif pn in history_norm:
            score += 4.0

    # 코드가 질문에 있는데 문서에 전혀 없으면 별도 penalty에서 크게 감점하므로 여기서는 cap만 적용합니다.
    return min(score, 36.0)


def _symptom_match_score(doc: dict[str, Any], hints: dict[str, Any]) -> float:
    haystack_error = _compact_norm(str(doc.get("에러명", "")))
    haystack_history = _compact_norm(str(doc.get("점검이력", "")))
    haystack_text = _compact_norm(str(doc.get("text", "")))
    if not (haystack_error or haystack_history or haystack_text):
        return 0.0
    score = 0.0
    for token in hints.get("symptom_keywords", []):
        token_norm = _compact_norm(token)
        if not token_norm:
            continue
        if token_norm in haystack_error:
            score += 2.4
        elif token_norm in haystack_history:
            score += 1.6
        elif token_norm in haystack_text:
            score += 0.7
    return min(score, 14.0)


def _keyword_coverage_score(doc: dict[str, Any], hints: dict[str, Any]) -> float:
    tokens = _unique_for_agent([
        *list(hints.get("error_tokens", []) or []),
        *list(hints.get("phrase_terms", []) or []),
        *list(hints.get("symptom_keywords", []) or []),
    ], limit=24)
    if not tokens:
        return 0.0

    error_text = _compact_norm(str(doc.get("에러명", "")))
    history_text = _compact_norm(str(doc.get("점검이력", "")))
    text_all = _compact_norm(" ".join([
        str(doc.get("text", "")),
        str(doc.get("에러명", "")),
        str(doc.get("점검이력", "")),
    ]))

    matched = 0
    score = 0.0
    for token in tokens:
        token_norm = _compact_norm(token)
        if not token_norm:
            continue
        if token_norm in error_text:
            matched += 1
            score += 1.7 if token_norm.isdigit() else 1.25
        elif token_norm in history_text:
            matched += 1
            score += 1.1 if token_norm.isdigit() else 0.8
        elif token_norm in text_all:
            matched += 1
            score += 0.35

    coverage = matched / max(len(tokens), 1)
    score += coverage * 5.0
    return min(score, 16.0)


def _explicit_mismatch_penalty(doc: dict[str, Any], hints: dict[str, Any]) -> float:
    penalty = 0.0
    line_text = _compact_norm(str(doc.get("라인", "")))
    equip_text = _compact_norm(str(doc.get("설비명", "")))
    error_history_text = _compact_norm(" ".join([
        str(doc.get("에러명", "")),
        str(doc.get("점검이력", "")),
        str(doc.get("text", "")),
    ]))

    line_tokens = [_compact_norm(t) for t in hints.get("line_tokens", []) if _compact_norm(t)]
    if line_tokens:
        line_ok = False
        for t in line_tokens:
            aliases = [_compact_norm(a) for a in _line_aliases(t)]
            if any(a and (a == line_text or a in line_text or line_text in a) for a in aliases):
                line_ok = True
                break
            # 설비명 내 2L/4L 같은 라인코드도 보조 허용
            if any(a and a in equip_text for a in aliases):
                line_ok = True
                break
        if not line_ok:
            penalty -= 9.0

    full_terms = [_compact_norm(t) for t in hints.get("equip_full_terms", []) if _compact_norm(t)]
    model_terms = [_compact_norm(t) for t in hints.get("equip_model_terms", []) if _compact_norm(t)]
    unit_terms = [_compact_norm(t) for t in hints.get("equip_unit_terms", []) if _compact_norm(t)]
    if full_terms and not any(t in equip_text for t in full_terms):
        # full 설비명이 명시된 경우 미일치는 강하게 감점
        penalty -= 18.0
    elif model_terms and not any(t in equip_text for t in model_terms):
        penalty -= 8.0
    elif model_terms and unit_terms and not any(t in equip_text for t in unit_terms):
        penalty -= 5.0

    error_tokens = [_compact_norm(t) for t in hints.get("error_tokens", []) if _compact_norm(t)]
    numeric_or_code = [t for t in error_tokens if t.isdigit() or re.fullmatch(r"x?\d{2,}[a-z0-9]*", t)]
    if numeric_or_code and not any(t in error_history_text for t in numeric_or_code):
        penalty -= 18.0
    elif error_tokens:
        # 단어형 에러 토큰이 많을 때 coverage가 너무 낮으면 감점합니다.
        hits = sum(1 for t in error_tokens[:16] if t in error_history_text)
        if hits == 0:
            penalty -= 7.0
        elif hits / max(len(error_tokens[:16]), 1) < 0.18:
            penalty -= 3.5

    return penalty


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
    structured_scores = [float(doc.get("structured_score", 0.0) or 0.0) for doc in candidates]
    max_structured_score = max(structured_scores) if structured_scores else 1.0

    dates = [d for d in (_safe_date(doc) for doc in candidates) if d is not None]
    newest = max(dates) if dates else None
    oldest = min(dates) if dates else None
    date_span = max((newest - oldest).days, 1) if newest and oldest else 1

    reranked: list[dict[str, Any]] = []
    for doc in candidates:
        # 정확한 행 선택이 목적이므로 컬럼 직접 매칭 점수를 가장 강하게 둡니다.
        # vector_score는 의미적 보조 점수로만 사용합니다.
        vector_score = 0.0
        if doc.get("similarity") is not None:
            vector_score = _normalize_similarity(float(doc.get("similarity", 9999.0) or 9999.0), min_sim, max_sim) * 1.2

        sparse_score = 0.0
        if max_keyword_score > 0:
            sparse_score = (float(doc.get("keyword_score", 0.0) or 0.0) / max_keyword_score) * 3.8

        structured_score = 0.0
        if max_structured_score > 0:
            structured_score = (float(doc.get("structured_score", 0.0) or 0.0) / max_structured_score) * 10.0

        channels = doc.get("retrieval_channels", []) or []
        retrieval_bonus = min(1.8, 0.45 * len(channels)) + (1.2 if "column" in channels else 0.0)
        query_hit_bonus = min(1.5, 0.25 * len(doc.get("query_hits", []) or []))

        equip_score = _field_match_score(question, str(doc.get("설비명", "")), 3.0, 0.8, 2.5)
        error_score = _field_match_score(question, str(doc.get("에러명", "")), 4.0, 1.0, 3.5)
        line_score = _field_match_score(question, str(doc.get("라인", "")), 1.3, 0.5, 1.4)
        history_score = _field_match_score(question, str(doc.get("점검이력", "")), 0.0, 0.24, 2.0)

        keyword_coverage = _keyword_coverage_score(doc, hints)
        column_boost = (
            _line_token_match_score(str(doc.get("라인", "")), hints, str(doc.get("설비명", "")))
            + _equipment_match_score(str(doc.get("설비명", "")), hints)
            + _error_token_match_score(str(doc.get("에러명", "")), hints, str(doc.get("점검이력", "")), str(doc.get("text", "")))
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

        mismatch_penalty = _explicit_mismatch_penalty(doc, hints)

        total = (
            structured_score
            + sparse_score
            + vector_score
            + retrieval_bonus
            + query_hit_bonus
            + equip_score
            + error_score
            + line_score
            + history_score
            + column_boost
            + memory_boost
            + focus_boost
            + date_score
            + mismatch_penalty
        )
        cloned = dict(doc)
        cloned["rerank"] = {
            "structured": round(structured_score, 3),
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
            "mismatch_penalty": round(mismatch_penalty, 3),
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
            line_norm = _compact_norm(line_val)
            equip_norm_for_line = _compact_norm(equip_val)
            for token in hints.get("line_tokens", []):
                aliases = [_compact_norm(a) for a in _line_aliases(token)]
                if any(a and (a == line_norm or a in line_norm or line_norm in a) for a in aliases):
                    boost += 5.0
                    break
                if any(a and a in equip_norm_for_line for a in aliases):
                    boost += 2.0
                    break
            line_scores[line_val] += base_weight + boost

        if equip_val:
            boost = 0.0
            if _question_mentions(equip_val, question):
                boost += 4.5
            equip_norm = _compact_norm(equip_val)
            full_terms = [_compact_norm(t) for t in hints.get("equip_full_terms", []) if _compact_norm(t)]
            model_terms = [_compact_norm(t) for t in hints.get("equip_model_terms", []) if _compact_norm(t)]
            unit_terms = [_compact_norm(t) for t in hints.get("equip_unit_terms", []) if _compact_norm(t)]
            if any(t in equip_norm for t in full_terms):
                boost += 8.0
            elif model_terms and any(t in equip_norm for t in model_terms):
                boost += 3.5
                if any(t in equip_norm for t in unit_terms):
                    boost += 4.0
            equip_scores[equip_val] += base_weight + boost

        if error_val:
            boost = 0.0
            if _question_mentions(error_val, question):
                boost += 4.5
            error_norm = _compact_norm(error_val)
            for token in hints.get("error_tokens", [])[:20]:
                token_norm = _compact_norm(token)
                if token_norm and token_norm in error_norm:
                    boost += 5.0 if token_norm.isdigit() or re.fullmatch(r"x?\d{2,}[a-z0-9]*", token_norm) else 2.5
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
    """2차 보강 검색도 최대 2개 질의로 제한합니다.

    1차에서 추정한 focus slot을 이용해 하나의 보강 질의를 만들고, 원 질문을 함께 사용합니다.
    보강 검색은 이미 1차 후보를 바탕으로 수행되므로 많은 질의를 반복할 필요가 없습니다.
    """
    queries: list[str] = [question]
    focused = " ".join(
        part for part in [
            process,
            focus_slots.get("line", ""),
            focus_slots.get("equip", ""),
            focus_slots.get("error", ""),
            " ".join(hints.get("symptom_keywords", [])[:5]),
        ] if part
    ).strip()
    if focused and _compact_norm(focused) != _compact_norm(question):
        queries.append(focused)

    deduped: list[str] = []
    seen = set()
    for item in queries:
        cleaned = re.sub(r"\s+", " ", item or "").strip()
        key = _compact_norm(cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= max(1, min(MAX_QUERY_VARIANTS, 2)):
            break
    return deduped

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


def _process_event(stage: str, label: str, detail: str = "", status: str = "running", meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": "process",
        "stage": stage,
        "label": label,
        "detail": detail,
        "status": status,
        "meta": meta or {},
    }


def _prepare_answer_context_with_progress(
    question: str,
    process: str,
    *,
    previous_state: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reference_doc_count: int | None = None,
) -> Generator[dict[str, Any], None, tuple[AgenticChatState, dict[str, Any]]]:
    """답변 생성 전 RAG 검색 과정을 단계별 이벤트로 노출하는 준비 함수.

    직접 답변 생성 함수는 기존처럼 _prepare_answer_context()를 사용하고,
    스트리밍 답변은 이 generator를 통해 현재 동작 중인 검색 프로세스를 UI에 표시합니다.
    """
    # 사용자 요청에 따라 "후속 질문" 분기/포맷을 더 이상 사용하지 않습니다.
    # 모든 질문은 독립적인 인폼노트 DB 질문으로 처리하고, 답변 포맷도 하나로 고정합니다.
    # 단, 답변 후 대화 메모리는 저장해 운영/디버깅 데이터로는 계속 활용합니다.
    memory = {"process": process}
    final_context_docs = _normalize_reference_doc_count(reference_doc_count)
    is_follow_up = False
    intent = "summary"

    yield _process_event(
        "question_analysis",
        "질문 해석",
        "라인/설비명/에러명/증상 키워드를 추출하고 검색 전략을 준비합니다.",
    )
    hints = _extract_structured_hints(question, memory, is_follow_up=False)

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
    yield _process_event(
        "question_analysis",
        "질문 해석 완료",
        f"검색 질의 {len(query_variants)}개를 생성했습니다.",
        status="done",
        meta={"query_variants": query_variants, "hints": hints},
    )

    adaptive_keyword_top_k = max(KEYWORD_TOP_K, final_context_docs * 3, 20)
    yield _process_event(
        "first_retrieval",
        "1차 후보 검색",
        "컬럼 우선 검색, text embedding 검색, keyword 검색을 함께 수행합니다.",
        meta={
            "dense_top_k_per_query": max(DENSE_TOP_K_PER_QUERY, min(10, final_context_docs)),
            "keyword_top_k": adaptive_keyword_top_k,
            "reference_doc_count": final_context_docs,
        },
    )
    first_candidates = hybrid_search_similar_documents(
        query_variants=query_variants,
        process=process,
        dense_top_k_per_query=max(DENSE_TOP_K_PER_QUERY, min(10, final_context_docs)),
        keyword_top_k=adaptive_keyword_top_k,
        query_hints=hints,
    )
    first_candidates = _dedup_preserve_order(first_candidates)[: max(FIRST_PASS_KEEP, final_context_docs)]
    yield _process_event(
        "first_retrieval",
        "1차 후보 검색 완료",
        f"후보 문서 {len(first_candidates)}개를 수집했습니다.",
        status="done",
        meta={"candidate_count_first": len(first_candidates)},
    )

    yield _process_event(
        "first_rerank",
        "1차 재랭킹",
        "라인/설비명/에러명/점검이력 매칭 점수와 유사도 점수를 합산합니다.",
    )
    first_ranked = _rerank_candidates(
        question,
        first_candidates,
        memory=memory,
        is_follow_up=is_follow_up,
        focus_slots={},
        hints=hints,
    )
    yield _process_event(
        "first_rerank",
        "1차 재랭킹 완료",
        f"상위 후보 {len(first_ranked)}개를 정렬했습니다.",
        status="done",
        meta={"ranked_count_first": len(first_ranked)},
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
            "retrieval_strategy": "column_first_current_table_step1",
        }
        yield _process_event(
            "no_result",
            "검색 결과 없음",
            "1차 검색에서 관련 후보를 찾지 못했습니다.",
            status="warning",
        )
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

    yield _process_event(
        "focus_inference",
        "검색 초점 추정",
        "상위 후보를 기준으로 라인/설비명/에러명 초점을 확인합니다.",
    )
    focus_slots, focus_sources = _infer_focus_slots(question, first_ranked, memory, is_follow_up=is_follow_up, hints=hints)
    second_queries = _build_second_pass_queries(question, process, focus_slots, is_follow_up=is_follow_up, hints=hints)
    yield _process_event(
        "focus_inference",
        "검색 초점 추정 완료",
        "명시 조건과 보강 검색 질의를 정리했습니다.",
        status="done",
        meta={"focus_slots": focus_slots, "focus_sources": focus_sources, "second_queries": second_queries},
    )

    # 사용자가 명시한 라인/설비만 DB filter로 사용합니다.
    # 후보에서 추정된 설비를 바로 filter로 걸면, 라인/설비명이 없는 에러명 질문에서
    # 첫 후보에 과도하게 끌려가 정확도가 떨어질 수 있습니다.
    line_filter = focus_slots.get("line", "") if focus_sources.get("line") == "question" else ""
    equip_filter = focus_slots.get("equip", "") if focus_sources.get("equip") == "question" else ""

    yield _process_event(
        "second_retrieval",
        "2차 보강 검색",
        "1차 후보에서 추정한 초점으로 컬럼/키워드/vector 후보를 추가 확인합니다.",
        meta={"line_filter": line_filter, "equip_filter": equip_filter},
    )
    second_candidates = hybrid_search_similar_documents(
        query_variants=second_queries,
        process=process,
        dense_top_k_per_query=max(DENSE_TOP_K_PER_QUERY, min(10, final_context_docs)),
        keyword_top_k=adaptive_keyword_top_k,
        line=line_filter,
        equip=equip_filter,
        query_hints=hints,
    )
    yield _process_event(
        "second_retrieval",
        "2차 보강 검색 완료",
        f"보강 후보 {len(second_candidates)}개를 수집했습니다.",
        status="done",
        meta={"candidate_count_second": len(second_candidates)},
    )

    yield _process_event(
        "final_rerank",
        "최종 재랭킹",
        "1차/2차 후보를 병합하고 최종 참조 문서를 선택합니다.",
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
    yield _process_event(
        "final_rerank",
        "최종 재랭킹 완료",
        f"최종 후보 {len(final_ranked)}개 중 참조 문서 {min(len(final_ranked), final_context_docs)}개를 선택합니다.",
        status="done",
        meta={"candidate_count_final": len(final_ranked), "reference_doc_count": final_context_docs},
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
            "retrieval_strategy": "column_first_current_table_step1",
        }
        yield _process_event(
            "no_result",
            "검색 결과 없음",
            "최종 재랭킹 후 사용할 참조 문서를 찾지 못했습니다.",
            status="warning",
        )
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

    yield _process_event(
        "evidence_build",
        "참조 문서 확정",
        "상위 문서에서 답변 근거와 참고 이력 표를 구성합니다.",
    )
    evidence_docs = final_ranked[:final_context_docs]
    selected_doc = evidence_docs[0]
    playbook = _build_playbook(evidence_docs)
    yield _process_event(
        "evidence_build",
        "참조 문서 확정 완료",
        f"참조 문서 {len(evidence_docs)}개를 확정했습니다.",
        status="done",
        meta={"selected_doc_id": selected_doc.get("id"), "document_count": len(evidence_docs)},
    )

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
        "retrieval_strategy": "column_first_current_table_step1",
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


def _prepare_answer_context(
    question: str,
    process: str,
    *,
    previous_state: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reference_doc_count: int | None = None,
) -> tuple[AgenticChatState, dict[str, Any]]:
    generator = _prepare_answer_context_with_progress(
        question,
        process,
        previous_state=previous_state,
        recent_messages=recent_messages,
        reference_doc_count=reference_doc_count,
    )
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value

def answer_question_stream(
    question: str,
    process: str,
    *,
    previous_state: dict[str, Any] | None = None,
    recent_messages: list[dict[str, Any]] | None = None,
    reference_doc_count: int | None = None,
) -> Generator[Any, None, AgenticChatState]:
    prepare_generator = _prepare_answer_context_with_progress(
        question,
        process,
        previous_state=previous_state,
        recent_messages=recent_messages,
        reference_doc_count=reference_doc_count,
    )

    while True:
        try:
            progress = next(prepare_generator)
        except StopIteration as stop:
            state, context = stop.value
            break
        yield progress

    if not state.get("selected_doc") or not context.get("evidence_docs"):
        fallback = state.get("llm_response") or "관련 인폼노트 이력을 찾지 못했습니다."
        state["llm_prompt"] = ""
        state["llm_response"] = fallback
        try:
            log_chat_history(question, fallback, [])
        except Exception as error:
            print(f"[WARN] log save failed: {error}")
        yield _process_event("finalize", "응답 정리", "검색 결과 없음 안내 문구를 전송합니다.", status="done")
        yield {"event": "delta", "content": fallback}
        return state

    try:
        yield _process_event(
            "prompt_build",
            "답변 프롬프트 구성",
            "확정된 참조 문서와 점검 패턴을 답변 생성용 컨텍스트로 정리합니다.",
        )
        prompt_template = SUMMARY_PROMPT
        prompt = prompt_template.format(
            question=question,
            conversation_memory="후속 질문 분기는 사용하지 않음",
            focus_slots=json.dumps(context["focus_slots"], ensure_ascii=False),
            playbook=_format_playbook(context["playbook"]),
            evidence=_build_evidence_block(context["evidence_docs"]),
        )
        state["llm_prompt"] = prompt
        yield _process_event("prompt_build", "답변 프롬프트 구성 완료", "LLM 스트리밍 답변을 시작합니다.", status="done")
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
        yield _process_event("prompt_build", "프롬프트 구성 오류", str(error), status="warning")
        yield {"event": "delta", "content": final_text}
        return state

    chunks: list[str] = []
    try:
        yield _process_event("llm_stream", "LLM 답변 생성", "텍스트 답변을 실시간으로 생성합니다.")
        for chunk in streaming_llm.stream([HumanMessage(content=prompt)]):
            content = getattr(chunk, "content", None)
            if content is None:
                content = str(chunk)
            if not content:
                continue
            chunks.append(content)
            yield {"event": "delta", "content": content}
        yield _process_event("llm_stream", "LLM 답변 생성 완료", "본문 생성을 완료하고 참고 정보를 정리합니다.", status="done")
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
        yield _process_event("llm_stream", "LLM 스트리밍 오류", str(error), status="warning")
        yield {"event": "delta", "content": final_text}
        return state

    draft = "".join(chunks).strip()
    if not draft:
        draft = "## 답변\n질문에 대한 답변을 생성하지 못했습니다.\n\n## 추가 확인\n- 잠시 후 다시 시도해 주세요."

    yield _process_event(
        "reference_finalize",
        "참조 정보 정리",
        "기본 정보와 관련 이력 표를 마지막에 한 번에 추가합니다.",
    )
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
        yield {"event": "delta", "content": tail}
    yield _process_event("reference_finalize", "참조 정보 정리 완료", "답변과 참고 이력 구성을 완료했습니다.", status="done")

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
