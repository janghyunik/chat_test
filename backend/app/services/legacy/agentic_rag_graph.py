import os
import re
import json
from typing import TypedDict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_community.chat_models.ollama import ChatOllama
from langchain.schema import HumanMessage
from langchain_core.prompts import PromptTemplate

from app.services.legacy.pg_vector_utils import search_similar_documents
from app.services.legacy.logging_utils import log_chat_history

# ==================== 설정 ====================
load_dotenv()
LLM_MODEL = os.getenv("LLM_MODEL")

# 1차 검색 후보 수
TOP_K = 10
RERANK_TOP_K = 5
# 2차 검색에서 컨텍스트로 사용할 문서 수(선택 문서 포함)
CONTEXT_USE_N = 5
# 2차 검색에서 벡터DB에서 가져올 후보 수(버킷/중복 제거 후 5건을 안정적으로 확보하려면 여유 있게)
SECOND_PASS_TOPK = 32

print(f"[INIT] LLM_MODEL={LLM_MODEL}, TOP_K={TOP_K}, CONTEXT_USE_N={CONTEXT_USE_N}, SECOND_PASS_TOPK={SECOND_PASS_TOPK}")

llm = ChatOllama(model=LLM_MODEL, temperature=0, stream=False)

# ==================== 상태 정의 ====================
class AgenticChatState(TypedDict):
    user_question: str
    current_step: str
    mode: str
    metadata: dict
    meta_confirmed: Optional[bool]
    user_message: str
    docs: List[dict]
    selected_doc: Optional[dict]
    llm_prompt: str
    llm_response: str
    process: str
    retry_count: int
    next_step: Optional[str]

# ==================== 유틸(로그/정규화/중복제거) ====================
def _snip(text: str, n: int = 120) -> str:
    s = str(text or "").replace("\n", " ").replace("|", "¦")
    return s if len(s) <= n else s[:n] + " …"

def _doc_line(d: dict, idx: Optional[int] = None) -> str:
    date = str(d.get("날짜", ""))[:10]
    equip = str(d.get("설비명", ""))
    err = str(d.get("에러명", ""))
    desc = _snip(d.get("점검이력", ""), 100)
    head = f"[{idx}] " if idx is not None else ""
    return f"{head}{date} | {equip} | {err} | {desc}"

def _norm(s: str) -> str:
    # 공백/개행/탭 정리 + 소문자화
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()

def _doc_key(d: dict):
    return (
        str(d.get("날짜","")),
        str(d.get("설비명","")),
        str(d.get("에러명","")),
        str(d.get("점검이력","")),
    )

def _dedup_preserve_order(docs: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for d in docs:
        k = _doc_key(d)
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out



STOPWORDS = {
    "이력", "오류", "에러", "알람", "점검", "정검", "조치", "문제", "발생", "현상",
    "관련", "내용", "알려줘", "알려주세요", "무엇", "뭐야", "조회", "정리", "확인",
    "호기", "설비", "장비", "라인", "please", "what", "show"
}

def _tokenize(text: str) -> List[str]:
    return [tok for tok in re.findall(r"[0-9A-Za-z가-힣]+", str(text or "").lower()) if tok]

def _keyword_tokens(question: str) -> List[str]:
    tokens = []
    for tok in _tokenize(question):
        if tok in STOPWORDS:
            continue
        if len(tok) >= 2 or tok.isdigit():
            tokens.append(tok)
    seen = set()
    ordered = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            ordered.append(tok)
    return ordered

def _safe_date(doc: dict) -> Optional[datetime]:
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
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        return None

def _normalize_similarity(similarity: float, min_sim: float, max_sim: float) -> float:
    if max_sim - min_sim < 1e-9:
        return 1.0
    # pgvector <=> 는 작을수록 더 유사함
    return 1.0 - ((similarity - min_sim) / (max_sim - min_sim))

def _field_match_score(question_norm: str, keywords: List[str], field_value: str, exact_weight: float, token_weight: float, cap: float) -> float:
    field_norm = _norm(field_value)
    if not field_norm:
        return 0.0
    score = 0.0
    if field_norm and field_norm in question_norm:
        score += exact_weight
    matched = sum(1 for tok in keywords if tok in field_norm)
    score += min(cap, matched * token_weight)
    return score

def _rerank_candidates(question: str, candidates: List[dict]) -> List[dict]:
    if not candidates:
        return []

    question_norm = _norm(question)
    keywords = _keyword_tokens(question)
    sims = [float(doc.get("similarity", 9999.0) or 9999.0) for doc in candidates]
    min_sim = min(sims)
    max_sim = max(sims)

    dates = [d for d in (_safe_date(doc) for doc in candidates) if d is not None]
    newest = max(dates) if dates else None
    oldest = min(dates) if dates else None
    date_span = max((newest - oldest).days, 1) if newest and oldest else 1

    reranked = []
    for doc in candidates:
        vector_score = _normalize_similarity(float(doc.get("similarity", 9999.0) or 9999.0), min_sim, max_sim) * 4.0
        equip_score = _field_match_score(question_norm, keywords, str(doc.get("설비명", "")), exact_weight=2.4, token_weight=0.7, cap=1.8)
        error_score = _field_match_score(question_norm, keywords, str(doc.get("에러명", "")), exact_weight=3.0, token_weight=0.8, cap=2.4)
        line_score = _field_match_score(question_norm, keywords, str(doc.get("라인", "")), exact_weight=0.8, token_weight=0.5, cap=0.8)
        history_score = _field_match_score(question_norm, keywords, str(doc.get("점검이력", "")), exact_weight=0.0, token_weight=0.22, cap=1.6)

        date_score = 0.0
        doc_date = _safe_date(doc)
        if newest and doc_date:
            recency = 1.0 - ((newest - doc_date).days / date_span)
            date_score = max(0.0, recency) * 0.9

        total_score = vector_score + equip_score + error_score + line_score + history_score + date_score
        debug = {
            "vector": round(vector_score, 3),
            "equip": round(equip_score, 3),
            "error": round(error_score, 3),
            "line": round(line_score, 3),
            "history": round(history_score, 3),
            "date": round(date_score, 3),
            "total": round(total_score, 3),
        }
        doc_copy = dict(doc)
        doc_copy["rerank"] = debug
        reranked.append(doc_copy)

    reranked.sort(key=lambda item: item.get("rerank", {}).get("total", 0.0), reverse=True)
    print(f"[RERANK] keywords={keywords}")
    for idx, doc in enumerate(reranked, 1):
        print(f"  #{idx} total={doc['rerank']['total']} sim={doc.get('similarity')} | {_doc_line(doc)} | details={doc['rerank']}")
    return reranked

# ==================== 고급 트러블슈팅 프롬프트 ====================
ENHANCED_TROUBLESHOOT_PROMPT = PromptTemplate(
    template=("""
당신은 반도체 설비 점검 이력과 사례를 분석해, 현장 엔지니어가 참고할 실질적 분석과 권고안을 제시하는 AI입니다.

[사용자 질문]
{query}

[관련 문서]
{document}

[요청] 핵심만 요약 후 체크리스트 제시.
""").strip(),
    input_variables=["equip", "err", "document", "topn"]
)

# ==================== 분류 ====================
def classify_question(state: AgenticChatState) -> AgenticChatState:
    print("\n[STEP] classify_question")
    question = state["user_question"]
    print(f"[INPUT] user_question: {question}")

    classify_prompt = """
    아래 사용자 질문이 어떤 유형에 속하는지 반드시 inform 또는 general 중 하나로만 분류하세요.

    1. inform:
    - 반도체 설비/장비/라인/호기/장치의 에러, 고장, 알람, 점검, 수리, 조치, 정비, 이력 등
    2. general:
    - 산업 현장 정비/설비/이력과 직접 관련 없는 질문 전부

    질문: {question}
    답변:
    """.strip().format(question=question)

    try:
        resp = llm.invoke([HumanMessage(content=classify_prompt)]).content.strip().lower()
    except Exception as e:
        print(f"[ERROR] classify_question LLM error: {e}")
        resp = "general"

    print(f"[LLM classify result]: {resp}")
    state["mode"] = "inform" if "inform" in resp or "인폼" in resp else "general"
    state["current_step"] = "rag_retrieve" if state["mode"] == "inform" else "handle_general"
    print(f"[OUTPUT] mode: {state['mode']}, process(공정명): {state['process']}, next_step: {state['current_step']}")
    return state

# ==================== 일반 질문 ====================
def handle_general(state: AgenticChatState) -> AgenticChatState:
    print("\n[STEP] handle_general")
    user_question = state.get("user_question", "")
    prompt = (
        f"""아래 사용자의 질문에 대해 **불필요한 서론·결론 없이** 꼭 필요한 정보만 간결하게 답변하세요.
가능하면 한두 문단 이내로 짧게 요약해 주세요.

질문: {user_question}
"""
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)]).content.strip()
    except Exception as e:
        print(f"[ERROR] handle_general LLM error: {e}")
        response = "죄송합니다. 답변 중 오류가 발생했습니다."
    state["llm_response"] = response
    state["current_step"] = "end"
    return state

# ==================== 1차 검색(표 제시) ====================
def rag_retrieve(state: AgenticChatState) -> AgenticChatState:
    print("\n[STEP] rag_retrieve")
    question = state.get("user_question", "")
    process = state.get("process", "INFORM").upper()
    try:
        candidates = search_similar_documents(
            user_query=question,
            process=process,
            top_k=TOP_K
        )

        if not candidates:
            state["llm_response"] = "❌ 관련 문서를 찾지 못했습니다."
            state["current_step"] = "end"
            return state

        # 후보 10개를 표로 안내
        topn = candidates[:10]
        state["docs"] = topn
        table_lines = [
            "| No | 날짜 | 설비명 | 에러명 | 점검이력(요약) |",
            "|---:|:-----|:------|:------|:-------------|"
        ]
        for i, d in enumerate(topn, start=1):
            date = str(d.get("날짜", ""))[:10]
            equip = str(d.get("설비명", ""))[:30]
            err = str(d.get("에러명", ""))[:30]
            desc = _snip(d.get("점검이력", ""), 120)
            table_lines.append(f"| {i} | {date} | {equip} | {err} | {desc} |")

        guide = (
            "다음은 질문과 유사한 점검 이력 상위 10건입니다.\n\n"
            "찾으시는 **문서 번호(예: 3)** 를 입력해 주세요.\n\n"
            + "\n".join(table_lines)
        )
        state["llm_response"] = guide
        state["current_step"] = "wait_for_doc_choice"
    except Exception as e:
        state["llm_response"] = f"❌ retrieval 오류: {e}"
        state["current_step"] = "end"
    return state

# ==================== (옵션) 바로 요약 ====================
def answer_with_llm(state: AgenticChatState) -> AgenticChatState:
    print("\n[STEP] answer_with_llm")
    docs = state.get("docs", [])
    question = state.get("user_question", "")
    doc_str = "\n\n".join([
        f"[날짜] {d.get('날짜','')}\n[설비명] {d.get('설비명','')}\n[에러명] {d.get('에러명','')}\n[점검이력]\n{d.get('점검이력','')}"
        for d in docs
    ])
    rag_prompt = PromptTemplate(
        template="""
당신은 반도체 설비 점검 이력과 사례를 분석해, 현장 엔지니어가 참고할 실질적 분석과 권고안을 제시하는 AI입니다.

[사용자 질문]
{query}

[관련 문서]
{document}

[요청] 핵심만 요약 후 체크리스트 제시.
""",
        input_variables=["query", "document"]
    )
    prompt = rag_prompt.format(query=question, document=doc_str)
    try:
        response = llm.invoke([HumanMessage(content=prompt)]).content.strip()
    except Exception:
        response = "죄송합니다. 답변 중 오류가 발생했습니다."
    state["llm_prompt"] = prompt
    state["llm_response"] = response
    state["current_step"] = "end"

    try:
        log_chat_history(state["user_question"], response, docs)
    except Exception as log_err:
        print(f"[WARN] 로그 저장 실패: {log_err}")

    return state

# ==================== 번호 입력 & 최종 답 생성 ====================
def _parse_first_int(text: str) -> Optional[int]:
    m = re.search(r"-?\d+", str(text or ""))
    return int(m.group()) if m else None

def handle_doc_confirm(state: AgenticChatState) -> AgenticChatState:
    print("\n[STEP] handle_doc_confirm")
    user_msg = (state.get("user_message") or state.get("user_question") or "").strip()
    docs = state.get("docs", []) or []
    n = _parse_first_int(user_msg)
    if n is None:
        state["llm_response"] = "⚠️ 숫자를 입력해 주세요. 예: 3"
        state["current_step"] = "wait_for_doc_choice"
        return state
    idx = n - 1 if 1 <= n <= len(docs) else n
    if idx < 0 or idx >= len(docs):
        state["llm_response"] = f"⚠️ 1~{len(docs)}(또는 0~{len(docs)-1}) 범위의 번호로 입력해 주세요."
        state["current_step"] = "wait_for_doc_choice"
        return state

    sel = docs[idx]
    state["selected_doc"] = sel
    state["current_step"] = "generate_final_answer"

    print(f"[SELECT] user pick = {user_msg} -> idx={idx}")
    print(f"[SELECTED DOC] {_doc_line(sel)}")
    return state

def generate_final_answer(state: AgenticChatState) -> AgenticChatState:
    print("\n[STEP] generate_final_answer")
    sel = state.get("selected_doc") or {}
    if not sel:
        state["llm_response"] = "⚠️ 선택된 문서가 없습니다. 번호를 먼저 입력해 주세요."
        state["current_step"] = "wait_for_doc_choice"
        return state

    equip_raw = str(sel.get("설비명",""))
    err_raw   = str(sel.get("에러명",""))
    hist      = str(sel.get("점검이력",""))
    process   = (state.get("process") or "INFORM").upper()

    equip = _norm(equip_raw)
    err   = _norm(err_raw)

    # ✅ 2차 재검색 쿼리: (원 질문 제외) 선택행 기반
    hist_snip = hist[:300]  # 너무 길면 검색 성능 저하 → 스니핑
    enriched_query = f"설비명:{equip_raw} 에러명:{err_raw} {hist_snip}"

    # 로그
    print(f"[RETRIEVE2] enriched_query = {_snip(enriched_query, 200)}")
    print(f"[FILTER] strict equip='{equip_raw}', err='{err_raw}'")

    # ✅ 2차 검색 실행 (후보 넉넉히 확보)
    cand = search_similar_documents(
        user_query=enriched_query,
        process=process,
        top_k=SECOND_PASS_TOPK
    )
    print(f"[RETRIEVE2] raw candidates = {len(cand)}")
    for i, d in enumerate(cand, 1):
        print("  ↳", _doc_line(d, i))

    # ✅ 중복 제거
    cand = _dedup_preserve_order(cand)
    print(f"[RETRIEVE2] deduped candidates = {len(cand)}")

    # ✅ 버킷 분류: 엄격(설비=동일 & 에러=동일) → 설비만 동일 → 에러만 동일 → 기타
    strict_bucket = []
    equip_bucket  = []
    err_bucket    = []
    other_bucket  = []
    key_sel = _doc_key(sel)

    for d in cand:
        if _doc_key(d) == key_sel:
            # 선택 문서와 동일한 행은 컨텍스트에 중복 삽입 방지
            continue
        dq = _norm(d.get("설비명",""))
        de = _norm(d.get("에러명",""))
        if dq == equip and de == err:
            strict_bucket.append(d)
        elif dq == equip:
            equip_bucket.append(d)
        elif de == err:
            err_bucket.append(d)
        else:
            other_bucket.append(d)

    print(f"[BUCKET] strict={len(strict_bucket)}, equip_only={len(equip_bucket)}, err_only={len(err_bucket)}, other={len(other_bucket)}")

    # ✅ 선택 문서를 맨 앞 + 버킷 순서대로 합쳐 상위 N 보장
    pooled = [sel] + strict_bucket + equip_bucket + err_bucket + other_bucket
    pooled = _dedup_preserve_order(pooled)
    use_docs = pooled[:CONTEXT_USE_N]

    print(f"[CONTEXT] using {len(use_docs)} docs (target {CONTEXT_USE_N}):")
    for i, d in enumerate(use_docs, 1):
        print("  •", _doc_line(d, i))

    # 상태에도 저장(로그/후처리 참고용)
    state["docs"] = use_docs

    # ===== 인용태그 [#n]이 포함된 문서 블록 생성 =====
    doc_blocks = []
    for i, d in enumerate(use_docs, 1):
        doc_blocks.append(
            f"[#{i}] 날짜: {d.get('날짜','')}\n"
            f"     설비명: {d.get('설비명','')}\n"
            f"     에러명: {d.get('에러명','')}\n"
            f"     점검이력:\n{d.get('점검이력','')}"
        )
    doc_str = "\n\n".join(doc_blocks)

    # ===== LLM 프롬프트 (고급 트러블슈팅 가이드) =====
    prompt = ENHANCED_TROUBLESHOOT_PROMPT.format(
        query=state.get("user_question", ""),
        document=doc_str,
    )

    try:
        response = llm.invoke([HumanMessage(content=prompt)]).content.strip()
    except Exception as e:
        print(f"[ERROR] final LLM error: {e}")
        response = "죄송합니다. 최종 답변 생성 중 오류가 발생했습니다."

    state["llm_prompt"] = prompt
    state["llm_response"] = response
    state["current_step"] = "end"

    # ✅ 대화 로그 저장 (질문/답변 및 최종 사용 문서들)
    try:
        log_chat_history(state["user_question"], response, use_docs)
    except Exception as log_err:
        print(f"[WARN] 로그 저장 실패: {log_err}")

    return state

def retrieve_and_answer_direct(state: AgenticChatState) -> AgenticChatState:
    """
    질문 즉시 top-k 후보를 가져온 뒤,
    설비명/에러명/키워드/날짜/유사도를 합산해 내부 재랭킹하고 최종 1개를 선택합니다.
    """
    print("\n[STEP] retrieve_and_answer_direct")
    question = state.get("user_question", "")
    process = (state.get("process") or "INFORM").upper()

    try:
        candidates = search_similar_documents(
            user_query=question,
            process=process,
            top_k=RERANK_TOP_K,
        )
    except Exception as e:
        state["llm_response"] = f"❌ retrieval 오류: {e}"
        state["current_step"] = "end"
        return state

    if not candidates:
        state["llm_response"] = "관련 점검 이력을 찾지 못했습니다. 설비명, 호기, 에러명 등을 조금 더 구체적으로 입력해 주세요."
        state["current_step"] = "end"
        return state

    deduped = _dedup_preserve_order(candidates)
    reranked = _rerank_candidates(question, deduped)
    state["docs"] = reranked
    state["selected_doc"] = reranked[0]
    state["current_step"] = "generate_final_answer"
    return generate_final_answer(state)


def answer_question_direct(question: str, process: str) -> AgenticChatState:
    state: AgenticChatState = {
        "user_question": question,
        "current_step": "classify_question",
        "mode": "",
        "metadata": {},
        "meta_confirmed": None,
        "user_message": question,
        "docs": [],
        "selected_doc": None,
        "llm_prompt": "",
        "llm_response": "",
        "process": process,
        "retry_count": 0,
        "next_step": None,
    }

    state = classify_question(state)
    if state.get("mode") == "inform":
        return retrieve_and_answer_direct(state)
    return handle_general(state)


# ==================== 그래프 빌드 ====================
def build_agentic_rag_graph():
    print("\n[GRAPH INIT] agentic_rag_graph build...")
    builder = StateGraph(AgenticChatState)

    builder.add_node("classify_question", classify_question)
    builder.add_node("rag_retrieve", rag_retrieve)
    builder.add_node("handle_general", handle_general)
    builder.add_node("answer_with_llm", answer_with_llm)
    builder.add_node("handle_doc_confirm", handle_doc_confirm)
    builder.add_node("generate_final_answer", generate_final_answer)

    builder.set_entry_point("classify_question")

    builder.add_conditional_edges(
        "classify_question",
        lambda state: ("rag_retrieve" if state["mode"] == "inform" else "handle_general"),
        {
            "rag_retrieve": "rag_retrieve",
            "handle_general": "handle_general",
        },
    )

    builder.add_edge("handle_general", END)
    builder.add_edge("answer_with_llm", END)
    # rag_retrieve → 사용자 선택 대기 → handle_doc_confirm → generate_final_answer

    print("[GRAPH INIT] agentic_rag_graph built successfully")
    return builder.compile()

agentic_rag_graph = build_agentic_rag_graph()
print("[INIT] agentic_rag_graph ready")
