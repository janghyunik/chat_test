import os
import re
from typing import Any, Iterable
from datetime import datetime

import ollama
import psycopg2
from dotenv import load_dotenv

load_dotenv()

PG_CONN_STR = os.getenv("PG_CONN_STR") or "host=localhost dbname=vectorDB user=postgres password=1541"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "granite-embedding:278m"

MAX_RETRIEVAL_QUERIES = int(os.getenv("RAG_MAX_QUERY_VARIANTS", "2"))

PROCESS_TO_TABLE = {
    "MP": "inform_embedding_mp",
    "DA": "inform_embedding_da",
    "SMT": "inform_embedding_smt",
}

# 검색에서 제외해도 되는 일반 표현입니다. 단, ERR/ERROR/ALARM/FAIL 같은 영문 오류 표현은
# 실제 에러명 컬럼에 자주 포함되므로 stopword로 제거하지 않습니다.
KEYWORD_STOPWORDS = {
    "이력", "점검", "점검이력", "조치", "발생", "현상", "관련", "내용",
    "알려줘", "알려주세요", "알려", "정리", "확인", "문의", "문제", "호기", "설비",
    "장비", "라인", "원인", "무엇", "뭐야", "조회", "요약", "경우", "대한", "기반",
    "please", "what", "show", "with", "from", "해줘", "있는", "했던", "되었던", "부분",
}

SAFE_FILTER_COLUMNS = {"라인", "공정", "설비명", "에러명", "source"}

COMMON_MODEL_STOPWORDS = {
    "ERR", "ERROR", "ALARM", "FAIL", "CHECK", "PICKER", "CHANGE", "SETUP", "TRAY",
    "LOADER", "INLET", "REEL", "PKG", "JOB", "SEND", "WAIT", "POS", "BLOCK", "PRINT",
}


def _get_table_name(process: str) -> str:
    process = (process or "").strip().upper()
    if process not in PROCESS_TO_TABLE:
        raise ValueError(f"❌ 지원하지 않는 공정명: {process}")
    return PROCESS_TO_TABLE[process]


def _tokenize(text: str) -> list[str]:
    return [tok for tok in re.findall(r"[0-9A-Za-z가-힣]+", str(text or "").lower()) if tok]


def _norm(text: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


def _unique(items: Iterable[Any], *, limit: int | None = None) -> list[str]:
    values: list[str] = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = _norm(text)
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(text)
        if limit is not None and len(values) >= limit:
            break
    return values


def _line_aliases(token: str) -> list[str]:
    raw = str(token or "").strip()
    if not raw:
        return []
    compact = _norm(raw)
    aliases: list[str] = [raw, compact]

    # 1L, 1line, 1라인 -> 1 / 1L / 1라인 / 1line 모두 후보로 확장합니다.
    m = re.fullmatch(r"([0-9]+)(?:l|line|라인)?", compact)
    if m:
        num = m.group(1)
        aliases.extend([num, f"{num}L", f"{num}l", f"{num}라인", f"{num}line"])

    # C5 같은 라인은 그대로 유지하되 C5라인 등도 보강합니다.
    m2 = re.fullmatch(r"([a-z]+[0-9]+)", compact)
    if m2:
        val = m2.group(1).upper()
        aliases.extend([val, f"{val}라인", f"{val}line"])
    return _unique(aliases, limit=12)


def _extract_line_from_equipment_suffix(suffix: str) -> list[str]:
    suffix_norm = _norm(suffix)
    # 2L02A, 4L15, 1L02 형태에서 앞의 2L/4L/1L을 라인으로 추출합니다.
    m = re.match(r"([0-9]+)l", suffix_norm)
    if m:
        return _line_aliases(m.group(1))
    return []


def _extract_equipment_candidates(text: str) -> dict[str, list[str]]:
    """질문에서 설비 모델/라인/호기 후보를 추출합니다.

    예시:
    - MTR-2L02A -> full=MTR-2L02A, model=MTR, suffix=2L02A, line=2
    - ATPS-1호기 -> full=ATPS-1, model=ATPS, suffix=1
    - E1-4L15 -> full=E1-4L15, model=E1, suffix=4L15, line=4
    - PT-4L02 -> full=PT-4L02, model=PT, suffix=4L02, line=4
    """
    raw = str(text or "")
    upper = raw.upper()
    full_terms: list[str] = []
    model_terms: list[str] = []
    suffix_terms: list[str] = []
    unit_terms: list[str] = []
    line_terms: list[str] = []

    # 모델-접미부 패턴. 접미부는 1, 02A, 1L02, 4L15 등 허용합니다.
    pattern = re.compile(r"\b([A-Z][A-Z0-9]{1,12})\s*[-_ ]\s*([0-9]+L?[0-9]*[A-Z]?|[0-9]+[A-Z]?)\s*(?:호기)?\b", re.IGNORECASE)
    for m in pattern.finditer(upper):
        model = m.group(1).upper()
        suffix = m.group(2).upper()
        if model in COMMON_MODEL_STOPWORDS:
            continue
        full_terms.extend([f"{model}-{suffix}", f"{model}{suffix}", f"{model} {suffix}"])
        model_terms.append(model)
        suffix_terms.append(suffix)
        unit_terms.append(suffix)
        # 2L02A -> 02A도 호기 후보로 보강합니다.
        lm = re.match(r"([0-9]+)L(.+)", suffix, flags=re.IGNORECASE)
        if lm:
            line_terms.extend(_line_aliases(lm.group(1)))
            if lm.group(2):
                unit_terms.append(lm.group(2).upper())

    # 하이픈 없이 ATPS 1호기 / ATPS1호기 같은 입력 보강
    pattern2 = re.compile(r"\b([A-Z][A-Z0-9]{1,12})\s*([0-9]+[A-Z]?)\s*호기\b", re.IGNORECASE)
    for m in pattern2.finditer(upper):
        model = m.group(1).upper()
        suffix = m.group(2).upper()
        if model in COMMON_MODEL_STOPWORDS:
            continue
        full_terms.extend([f"{model}-{suffix}", f"{model}{suffix}"])
        model_terms.append(model)
        suffix_terms.append(suffix)
        unit_terms.append(suffix)

    # 모델명만 언급한 경우. 너무 일반적인 영문 오류 토큰은 제외합니다.
    for token in re.findall(r"\b[A-Z][A-Z0-9]{1,12}\b", upper):
        if token in COMMON_MODEL_STOPWORDS:
            continue
        # X0306/X0119 같은 에러코드는 설비 모델로 보지 않습니다.
        if re.fullmatch(r"X[0-9]{3,}", token):
            continue
        model_terms.append(token)

    return {
        "full": _unique(full_terms, limit=10),
        "model": _unique(model_terms, limit=8),
        "suffix": _unique(suffix_terms, limit=8),
        "unit": _unique(unit_terms, limit=10),
        "line_from_equip": _unique(line_terms, limit=8),
    }


def _extract_line_terms(text: str) -> list[str]:
    raw = str(text or "")
    terms: list[str] = []

    for m in re.findall(r"([0-9]+|[A-Za-z][0-9]+)\s*(?:라인|line)\b", raw, flags=re.IGNORECASE):
        terms.extend(_line_aliases(m))
    for m in re.findall(r"\b([0-9]+)\s*[Ll]\b", raw):
        terms.extend(_line_aliases(m))
    for m in re.findall(r"\b([A-Za-z][0-9]+)\b", raw):
        if _norm(m).startswith("x"):
            continue
        # C5 같은 라인만 보강. E1은 설비 모델일 수 있으므로 단독 라인 후보로 과하게 쓰지 않습니다.
        if re.fullmatch(r"[Cc][0-9]+", m):
            terms.extend(_line_aliases(m))

    equip_parts = _extract_equipment_candidates(raw)
    terms.extend(equip_parts.get("line_from_equip", []))
    return _unique(terms, limit=14)


def _build_ngram_terms(tokens: list[str], *, min_n: int = 2, max_n: int = 4, limit: int = 16) -> list[str]:
    grams: list[str] = []
    filtered = [t for t in tokens if t and t not in KEYWORD_STOPWORDS]
    for n in range(min_n, max_n + 1):
        for i in range(0, max(0, len(filtered) - n + 1)):
            gram = " ".join(filtered[i:i+n]).strip()
            if len(_norm(gram)) >= 3:
                grams.append(gram)
    return _unique(grams, limit=limit)


def extract_query_keywords(text: str, *, limit: int = 8) -> list[str]:
    keywords: list[str] = []
    seen = set()
    for token in _tokenize(text):
        if token in KEYWORD_STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def embed_query(query: str, model: str = EMBEDDING_MODEL) -> list[float]:
    response = ollama.embeddings(model=model, prompt=query)
    return response["embedding"]


def _base_result(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "text": row[1],
        "날짜": row[2],
        "라인": row[3],
        "공정": row[4],
        "설비명": row[5],
        "에러명": row[6],
        "점검이력": row[7],
        "source": row[8],
    }


def _apply_meta_filters(filters: list[str], params: list[Any], line: str, equip: str, extra_filters: dict[str, Any] | None) -> None:
    if line:
        filters.append('라인 = %s')
        params.append(line)
    if equip:
        filters.append('설비명 = %s')
        params.append(equip)
    if extra_filters:
        for key, value in extra_filters.items():
            if key not in SAFE_FILTER_COLUMNS:
                continue
            if value in (None, ""):
                continue
            filters.append(f'{key} = %s')
            params.append(value)


def _terms_from_hints(user_query: str, query_hints: dict[str, Any] | None) -> dict[str, list[str]]:
    """질문을 DB 컬럼 검색용 토큰으로 분해합니다.

    Step 25 개선 포인트:
    - 1L/1라인/1line/C5 같은 라인 표현을 canonical alias로 확장합니다.
    - MTR-2L02A, ATPS-1, E1-4L15, PT-4L02 같은 설비명 패턴을 full/model/unit으로 분리합니다.
    - 에러명은 단일 토큰뿐 아니라 2~4gram phrase까지 생성해 에러명 컬럼 직접 매칭을 강화합니다.
    """
    hints = query_hints or {}
    keywords = extract_query_keywords(user_query, limit=20)
    tokens = _tokenize(user_query)
    ngrams = _build_ngram_terms(tokens, min_n=2, max_n=4, limit=20)
    numeric_tokens = re.findall(r"\b\d{2,}\b", str(user_query or ""))[:8]
    code_tokens = re.findall(r"\b[A-Za-z]+[0-9]{2,}[A-Za-z0-9]*\b", str(user_query or ""), flags=re.IGNORECASE)[:8]

    equip_parts = _extract_equipment_candidates(user_query)

    line_terms = _unique([
        *hints.get("line_tokens", []),
        *_extract_line_terms(user_query),
        *equip_parts.get("line_from_equip", []),
    ], limit=18)

    equip_terms = _unique([
        *hints.get("equip_full_terms", []),
        *hints.get("equip_tokens", []),
        *hints.get("hoigi_tokens", []),
        *equip_parts.get("full", []),
        *equip_parts.get("model", []),
        *equip_parts.get("unit", []),
    ], limit=22)

    # 에러명 컬럼은 phrase matching이 중요합니다. 예: "부자재 공급 에러", "라벨 PICKER 진공 ERROR".
    error_terms = _unique([
        *hints.get("error_terms", []),
        *hints.get("error_tokens", []),
        *code_tokens,
        *numeric_tokens,
        *ngrams,
        *[k for k in keywords if k not in equip_terms],
    ], limit=32)

    symptom_terms = _unique([
        *hints.get("symptom_keywords", []),
        *code_tokens,
        *ngrams[:10],
        *keywords,
    ], limit=28)

    if not error_terms:
        error_terms = _unique([k for k in keywords if k not in KEYWORD_STOPWORDS], limit=12)

    return {
        "line": line_terms,
        "equip": equip_terms,
        "equip_full": _unique([*hints.get("equip_full_terms", []), *equip_parts.get("full", [])], limit=12),
        "equip_model": _unique([*hints.get("equip_model_terms", []), *equip_parts.get("model", [])], limit=10),
        "equip_unit": _unique([*hints.get("equip_unit_terms", []), *equip_parts.get("unit", [])], limit=12),
        "error": error_terms,
        "symptom": symptom_terms,
        "keyword": _unique(keywords, limit=20),
        "codes": _unique([*code_tokens, *numeric_tokens], limit=12),
        "ngrams": ngrams,
    }


def search_similar_documents(
    user_query: str,
    process: str,
    top_k: int = 10,
    line: str = "",
    equip: str = "",
    extra_filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    table_name = _get_table_name(process)
    query_vector = embed_query(user_query)

    filters: list[str] = []
    meta_params: list[Any] = []
    _apply_meta_filters(filters, meta_params, line, equip, extra_filters)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    params = [query_vector] + meta_params + [query_vector, top_k]

    with psycopg2.connect(PG_CONN_STR) as conn:
        with conn.cursor() as cur:
            sql = f"""
            SELECT id, text, 날짜, 라인, 공정, 설비명, 에러명, 점검이력, source,
                   (embedding <=> %s::vector) AS similarity
            FROM {table_name}
            {where_clause}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """
            cur.execute(sql, params)
            rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        doc = _base_result(row)
        doc["similarity"] = row[9]
        doc["retrieval_channels"] = ["dense"]
        doc["query_hits"] = [user_query]
        results.append(doc)
    return results


def search_column_priority_documents(
    user_query: str,
    process: str,
    *,
    top_k: int = 30,
    line: str = "",
    equip: str = "",
    extra_filters: dict[str, Any] | None = None,
    query_hints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """현재 inform_embedding_* 테이블을 그대로 사용하는 컬럼 우선 검색입니다.

    이번 버전은 사용자가 제공한 실제 설비/라인/에러 표현을 반영했습니다.
    - 라인: 1, 2, 3, 4, C5 / 1L, 1라인, 1line 모두 인식
    - 설비명: MTR-2L02A, ATPS-1, E1-4L15, PT-4L02 패턴을 full/model/unit으로 분리
    - 에러명: code, 영문 대문자 토큰, 2~4gram phrase를 에러명 컬럼에 강하게 매칭
    """
    table_name = _get_table_name(process)
    terms = _terms_from_hints(user_query, query_hints)

    score_terms: list[str] = []
    score_params: list[Any] = []
    match_terms: list[str] = []
    match_params: list[Any] = []

    def add_like_score(column: str, term: str, weight: int, *, match: bool = True) -> None:
        if not term:
            return
        pattern = f"%{term}%"
        score_terms.append(f"CASE WHEN {column} ILIKE %s THEN {weight} ELSE 0 END")
        score_params.append(pattern)
        if match:
            match_terms.append(f"{column} ILIKE %s")
            match_params.append(pattern)

    # 1) 라인: 1L/1라인/1line 같은 표현은 라인 컬럼과 설비명 내부 라인코드 둘 다 확인합니다.
    for term in terms["line"]:
        add_like_score("라인", term, 120)
        add_like_score("설비명", term, 35, match=False)
        add_like_score("text", term, 20, match=False)

    # 2) 설비명: full term이 가장 중요하고, 모델/호기는 보조 점수입니다.
    for term in terms.get("equip_full", []):
        add_like_score("설비명", term, 260)
        add_like_score("text", term, 80)
        # 하이픈 제거 버전도 text/설비명에서 잡히도록 보강합니다.
        compact = _norm(term)
        if compact and compact != _norm(term.replace("-", "")):
            add_like_score("설비명", compact, 120, match=False)

    for term in terms.get("equip_model", []):
        add_like_score("설비명", term, 115)
        add_like_score("text", term, 35)
        add_like_score("점검이력", term, 20, match=False)

    for term in terms.get("equip_unit", []):
        # unit 단독 매칭은 위험하므로 낮은 점수로만 보조합니다.
        if len(_norm(term)) >= 2:
            add_like_score("설비명", term, 45)
            add_like_score("text", term, 15, match=False)

    for term in terms["equip"]:
        add_like_score("설비명", term, 80)
        add_like_score("text", term, 25)

    # 3) 에러명: 코드/phrase/에러명 키워드를 embedding보다 훨씬 강하게 반영합니다.
    for term in terms["error"]:
        compact = _norm(term)
        if not compact:
            continue
        is_code = bool(re.fullmatch(r"x?\d{3,}[a-z0-9]*", compact)) or compact.isdigit()
        is_phrase = " " in str(term).strip() or len(compact) >= 5
        error_weight = 340 if is_code else (260 if is_phrase else 170)
        add_like_score("에러명", term, error_weight)
        add_like_score("점검이력", term, 95 if is_code else 60)
        add_like_score("text", term, 110 if is_code else 65)

    # 4) 증상/조치 표현: 점검이력 중심으로 넓게 보강하되, 에러명에 있으면 강하게 봅니다.
    for term in terms["symptom"][:28]:
        compact = _norm(term)
        if not compact:
            continue
        add_like_score("에러명", term, 120 if len(compact) >= 4 else 80)
        add_like_score("점검이력", term, 105 if len(compact) >= 4 else 70)
        add_like_score("text", term, 45)

    # 5) 일반 keyword는 낮은 가중치로 누락 보완합니다.
    for term in terms["keyword"][:18]:
        add_like_score("에러명", term, 75)
        add_like_score("설비명", term, 55)
        add_like_score("점검이력", term, 55)
        add_like_score("text", term, 25)

    if not match_terms:
        return []

    filters: list[str] = [f"({' OR '.join(match_terms)})"]
    meta_params: list[Any] = []
    _apply_meta_filters(filters, meta_params, line, equip, extra_filters)

    where_clause = f"WHERE {' AND '.join(filters)}"
    score_expr = " + ".join(score_terms) if score_terms else "0"
    params = score_params + match_params + meta_params + [top_k]

    with psycopg2.connect(PG_CONN_STR) as conn:
        with conn.cursor() as cur:
            sql = f"""
            SELECT id, text, 날짜, 라인, 공정, 설비명, 에러명, 점검이력, source,
                   ({score_expr}) AS structured_score
            FROM {table_name}
            {where_clause}
            ORDER BY structured_score DESC, 날짜 DESC
            LIMIT %s
            """
            cur.execute(sql, params)
            rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        doc = _base_result(row)
        doc["structured_score"] = float(row[9] or 0.0)
        doc["retrieval_channels"] = ["column"]
        doc["query_hits"] = [user_query]
        doc["column_terms"] = terms
        results.append(doc)
    return results


def search_keyword_documents(
    user_query: str,
    process: str,
    *,
    top_k: int = 10,
    line: str = "",
    equip: str = "",
    extra_filters: dict[str, Any] | None = None,
    keywords: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    table_name = _get_table_name(process)
    kw_list = [kw for kw in (keywords or extract_query_keywords(user_query, limit=8)) if kw]
    if not kw_list:
        return []

    # phrase도 keyword 검색에 포함해 에러명/점검이력에서 phrase match를 더 잘 잡습니다.
    kw_list = _unique([*kw_list, *_build_ngram_terms(_tokenize(user_query), min_n=2, max_n=3, limit=10)], limit=18)

    score_terms: list[str] = []
    score_params: list[Any] = []
    match_terms: list[str] = []
    match_params: list[Any] = []

    for kw in kw_list:
        pattern = f"%{kw}%"
        score_terms.append(
            "("
            "CASE WHEN 에러명 ILIKE %s THEN 22 ELSE 0 END + "
            "CASE WHEN 설비명 ILIKE %s THEN 10 ELSE 0 END + "
            "CASE WHEN 라인 ILIKE %s THEN 6 ELSE 0 END + "
            "CASE WHEN 점검이력 ILIKE %s THEN 10 ELSE 0 END + "
            "CASE WHEN text ILIKE %s THEN 5 ELSE 0 END"
            ")"
        )
        score_params.extend([pattern, pattern, pattern, pattern, pattern])

        match_terms.append(
            "("
            "설비명 ILIKE %s OR 에러명 ILIKE %s OR 라인 ILIKE %s OR 점검이력 ILIKE %s OR text ILIKE %s"
            ")"
        )
        match_params.extend([pattern, pattern, pattern, pattern, pattern])

    filters: list[str] = [f"({' OR '.join(match_terms)})"]
    meta_params: list[Any] = []
    _apply_meta_filters(filters, meta_params, line, equip, extra_filters)

    where_clause = f"WHERE {' AND '.join(filters)}"
    score_expr = ' + '.join(score_terms) if score_terms else '0'
    params = score_params + match_params + meta_params + [top_k]

    with psycopg2.connect(PG_CONN_STR) as conn:
        with conn.cursor() as cur:
            sql = f"""
            SELECT id, text, 날짜, 라인, 공정, 설비명, 에러명, 점검이력, source,
                   ({score_expr}) AS keyword_score
            FROM {table_name}
            {where_clause}
            ORDER BY keyword_score DESC, 날짜 DESC
            LIMIT %s
            """
            cur.execute(sql, params)
            rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        doc = _base_result(row)
        doc["keyword_score"] = row[9]
        doc["retrieval_channels"] = ["keyword"]
        doc["query_hits"] = [user_query]
        results.append(doc)
    return results


def _doc_key(doc: dict[str, Any]) -> tuple[Any, ...]:
    return (
        doc.get("id"),
        doc.get("날짜"),
        doc.get("라인"),
        doc.get("공정"),
        doc.get("설비명"),
        doc.get("에러명"),
        doc.get("점검이력"),
    )


def _merge_doc(existing: dict[str, Any], incoming: dict[str, Any], channel_label: str, query_text: str) -> dict[str, Any]:
    if incoming.get("similarity") is not None:
        current = existing.get("similarity")
        incoming_sim = incoming.get("similarity")
        if current is None or (incoming_sim is not None and incoming_sim < current):
            existing["similarity"] = incoming_sim

    if incoming.get("keyword_score") is not None:
        existing["keyword_score"] = max(float(existing.get("keyword_score", 0.0) or 0.0), float(incoming.get("keyword_score", 0.0) or 0.0))

    if incoming.get("structured_score") is not None:
        existing["structured_score"] = max(float(existing.get("structured_score", 0.0) or 0.0), float(incoming.get("structured_score", 0.0) or 0.0))

    channels = list(existing.get("retrieval_channels", []))
    if channel_label not in channels:
        channels.append(channel_label)
    existing["retrieval_channels"] = channels

    hits = list(existing.get("query_hits", []))
    if query_text not in hits:
        hits.append(query_text)
    existing["query_hits"] = hits

    existing["retrieval_count"] = int(existing.get("retrieval_count", 0) or 0) + 1
    return existing


def hybrid_search_similar_documents(
    query_variants: list[str],
    process: str,
    *,
    dense_top_k_per_query: int = 6,
    keyword_top_k: int = 10,
    line: str = "",
    equip: str = "",
    extra_filters: dict[str, Any] | None = None,
    query_hints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """컬럼 우선 + dense + keyword 후보를 병합합니다.

    Step26 성능 최적화:
    - 질의는 최대 2개만 사용합니다.
    - 컬럼 우선 검색은 두 질의를 합친 combined query로 1회만 수행합니다.
    - keyword 검색도 combined query로 1회만 수행합니다.
    - dense/vector 검색만 최대 2회 수행합니다.

    이전처럼 8~10개 질의에 대해 dense/keyword 검색을 반복하면 Ollama embedding 호출과
    PostgreSQL ILIKE 검색이 반복되어 1차 후보 검색 시간이 길어집니다. 이 함수는 검색 품질을
    크게 해치지 않는 선에서 반복 검색 횟수를 줄이는 것을 목표로 합니다.
    """
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}

    unique_queries: list[str] = []
    seen = set()
    max_queries = max(1, min(MAX_RETRIEVAL_QUERIES, 2))
    for query in query_variants:
        query = re.sub(r"\s+", " ", str(query or "")).strip()
        key = _norm(query)
        if not query or key in seen:
            continue
        seen.add(key)
        unique_queries.append(query)
        if len(unique_queries) >= max_queries:
            break

    if not unique_queries:
        return []

    combined_query = " ".join(unique_queries)

    # 1) 컬럼 우선 검색: 라인/설비명/에러명/점검이력 직접 매칭은 combined query로 1회 수행합니다.
    try:
        column_docs = search_column_priority_documents(
            user_query=combined_query,
            process=process,
            top_k=max(keyword_top_k * 3, 60),
            line=line,
            equip=equip,
            extra_filters=extra_filters,
            query_hints=query_hints,
        )
    except Exception as error:
        print(f"[WARN] column-first retrieval failed for query={combined_query!r}: {error}")
        column_docs = []

    for doc in column_docs:
        key = _doc_key(doc)
        if key not in merged:
            merged[key] = dict(doc)
            merged[key]["retrieval_count"] = 0
        merged[key] = _merge_doc(merged[key], doc, "column", combined_query)

    # 2) dense/vector 검색: 의미 검색은 비용이 크므로 최대 2개 질의만 수행합니다.
    for query in unique_queries:
        try:
            dense_docs = search_similar_documents(
                user_query=query,
                process=process,
                top_k=dense_top_k_per_query,
                line=line,
                equip=equip,
                extra_filters=extra_filters,
            )
        except Exception as error:
            print(f"[WARN] dense retrieval failed for query={query!r}: {error}")
            dense_docs = []

        for doc in dense_docs:
            key = _doc_key(doc)
            if key not in merged:
                merged[key] = dict(doc)
                merged[key]["retrieval_count"] = 0
            merged[key] = _merge_doc(merged[key], doc, "dense", query)

    # 3) keyword 검색: 여러 질의별 반복 대신 combined query로 1회만 수행합니다.
    try:
        keyword_docs = search_keyword_documents(
            user_query=combined_query,
            process=process,
            top_k=max(keyword_top_k, 20),
            line=line,
            equip=equip,
            extra_filters=extra_filters,
            keywords=extract_query_keywords(combined_query, limit=14),
        )
    except Exception as error:
        print(f"[WARN] keyword retrieval failed for query={combined_query!r}: {error}")
        keyword_docs = []

    for doc in keyword_docs:
        key = _doc_key(doc)
        if key not in merged:
            merged[key] = dict(doc)
            merged[key]["retrieval_count"] = 0
        merged[key] = _merge_doc(merged[key], doc, "keyword", combined_query)

    results = list(merged.values())
    results.sort(
        key=lambda item: (
            float(item.get("structured_score", 0.0) or 0.0),
            int(item.get("retrieval_count", 0) or 0),
            len(item.get("retrieval_channels", []) or []),
            float(item.get("keyword_score", 0.0) or 0.0),
            -(float(item.get("similarity", 9999.0) or 9999.0)),
        ),
        reverse=True,
    )
    return results

def hybrid_search_similar_documents_legacy(error_name: str, process: str, line: str = "", equip: str = "", top_k: int = 10):
    return search_similar_documents(
        user_query=error_name,
        process=process,
        top_k=top_k,
        line=line,
        equip=equip,
    )

# -----------------------------------------------------------------------------
# Alert-specific retrieval
# -----------------------------------------------------------------------------

def _canonical_line_for_alert(line: Any) -> list[str]:
    """Alert 입력 라인 값을 DB 라인 컬럼 후보로 확장합니다."""
    raw = str(line or "").strip()
    if not raw:
        return []
    return _line_aliases(raw)


def _equipment_model_for_alert(equipment: Any) -> str:
    raw = str(equipment or "").strip().upper()
    m = re.match(r"^([A-Z0-9]+)", raw)
    return m.group(1) if m else ""


def _alert_error_terms(error_name: str) -> list[str]:
    tokens = _tokenize(error_name)
    codes = re.findall(r"\b[A-Za-z]+\d{2,}[A-Za-z0-9]*\b|\b\d{2,}\b", str(error_name or ""), flags=re.IGNORECASE)
    ngrams = _build_ngram_terms(tokens, min_n=2, max_n=4, limit=18)
    keywords = [kw for kw in extract_query_keywords(error_name, limit=16) if kw not in KEYWORD_STOPWORDS]
    return _unique([str(error_name or "").strip(), *codes, *ngrams, *keywords], limit=28)


def search_alert_precision_documents(
    *,
    line: str,
    equipment: str,
    error_name: str,
    process: str = "MP",
    top_k: int = 10,
    include_model_expansion: bool = True,
) -> list[dict[str, Any]]:
    """설비 에러 알림용 정밀 검색입니다.

    일반 채팅 질문과 달리 alert 입력은 line/equipment/error_name이 구조화되어 있습니다.
    따라서 line/equipment는 embedding이 아니라 SQL 컬럼 조건과 문자열 매칭으로 강하게 고정하고,
    error_name에 대해서만 keyword + 기존 full embedding 검색을 보조로 사용합니다.

    검색 단계:
    1. 동일 라인 + 동일 설비 후보를 우선 조회하고, 그 안에서 error_name/점검이력 매칭과 최신성을 반영합니다.
    2. 같은 설비의 최근 이력을 보강합니다.
    3. 결과가 부족하면 동일 모델 + error term 후보를 보강합니다.
    4. error_name만 embedding 검색하여 오타/축약 표현을 보완합니다.
    """
    table_name = _get_table_name(process)
    line_aliases = _canonical_line_for_alert(line)
    equip_raw = str(equipment or "").strip()
    equip_norm = _norm(equip_raw)
    model = _equipment_model_for_alert(equip_raw)
    error_terms = _alert_error_terms(error_name)
    error_norm = _norm(error_name)

    # line/equipment 조건은 SQL에서 강한 점수로 반영합니다. 단, 실제 DB 표기가 약간 다를 수 있어
    # WHERE 자체는 너무 좁히지 않고 점수 기반 정렬로 처리합니다.
    score_exprs: list[str] = []
    score_params: list[Any] = []
    match_exprs: list[str] = []
    match_params: list[Any] = []

    def add_score(expr: str, params: list[Any]) -> None:
        score_exprs.append(expr)
        score_params.extend(params)

    def add_match(expr: str, params: list[Any]) -> None:
        match_exprs.append(expr)
        match_params.extend(params)

    if line_aliases:
        add_score("CASE WHEN 라인 = ANY(%s) THEN 520 ELSE 0 END", [line_aliases])
        # 설비명 안의 2L/4L 코드도 보조로 반영합니다.
        for la in line_aliases[:8]:
            add_score("CASE WHEN 설비명 ILIKE %s THEN 90 ELSE 0 END", [f"%{la}%"])
        add_match("라인 = ANY(%s)", [line_aliases])

    if equip_raw:
        add_score("CASE WHEN 설비명 = %s THEN 760 ELSE 0 END", [equip_raw])
        add_score("CASE WHEN REPLACE(LOWER(설비명), '-', '') = %s THEN 720 ELSE 0 END", [equip_norm])
        add_score("CASE WHEN 설비명 ILIKE %s THEN 560 ELSE 0 END", [f"%{equip_raw}%"])
        add_match("(설비명 = %s OR REPLACE(LOWER(설비명), '-', '') = %s OR 설비명 ILIKE %s)", [equip_raw, equip_norm, f"%{equip_raw}%"])

    if model:
        add_score("CASE WHEN 설비명 ILIKE %s THEN 110 ELSE 0 END", [f"{model}%"])
        if include_model_expansion:
            add_match("설비명 ILIKE %s", [f"{model}%"])

    for term in error_terms:
        t_norm = _norm(term)
        if not t_norm:
            continue
        is_code = bool(re.fullmatch(r"x?\d{3,}[a-z0-9]*", t_norm)) or t_norm.isdigit()
        is_phrase = " " in str(term).strip() or len(t_norm) >= 5
        ew = 520 if is_code else (380 if is_phrase else 230)
        add_score("CASE WHEN 에러명 ILIKE %s THEN %s ELSE 0 END", [f"%{term}%", ew])
        add_score("CASE WHEN 점검이력 ILIKE %s THEN %s ELSE 0 END", [f"%{term}%", 180 if is_code else 95])
        add_score("CASE WHEN text ILIKE %s THEN %s ELSE 0 END", [f"%{term}%", 120 if is_code else 70])
        add_match("(에러명 ILIKE %s OR 점검이력 ILIKE %s OR text ILIKE %s)", [f"%{term}%", f"%{term}%", f"%{term}%"])

    if error_norm:
        add_score("CASE WHEN REPLACE(LOWER(에러명), ' ', '') ILIKE %s THEN 440 ELSE 0 END", [f"%{error_norm}%"])

    if not match_exprs:
        return []

    score_expr = " + ".join(score_exprs) if score_exprs else "0"
    where_clause = " OR ".join(match_exprs)
    limit = max(top_k * 5, 50)

    with psycopg2.connect(PG_CONN_STR) as conn:
        with conn.cursor() as cur:
            sql = f"""
            SELECT id, text, 날짜, 라인, 공정, 설비명, 에러명, 점검이력, source,
                   ({score_expr}) AS alert_score,
                   CASE
                     WHEN 라인 = ANY(%s) AND (설비명 = %s OR REPLACE(LOWER(설비명), '-', '') = %s) THEN '동일 라인 + 동일 설비'
                     WHEN (설비명 = %s OR REPLACE(LOWER(설비명), '-', '') = %s OR 설비명 ILIKE %s) THEN '동일 설비'
                     WHEN 설비명 ILIKE %s THEN '동일 모델'
                     ELSE '유사 이력'
                   END AS match_level
            FROM {table_name}
            WHERE ({where_clause})
            ORDER BY alert_score DESC, 날짜 DESC
            LIMIT %s
            """
            cur.execute(
                sql,
                score_params
                + [line_aliases or [str(line or "")], equip_raw, equip_norm, equip_raw, equip_norm, f"%{equip_raw}%", f"{model}%"]
                + match_params
                + [limit],
            )
            rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        doc = _base_result(row)
        doc["alert_score"] = float(row[9] or 0.0)
        doc["structured_score"] = max(float(doc.get("structured_score", 0.0) or 0.0), float(row[9] or 0.0))
        doc["match_level"] = row[10]
        doc["retrieval_channels"] = ["alert-column"]
        doc["query_hits"] = [f"{line} {equipment} {error_name}".strip()]
        results.append(doc)

    # error_name embedding 보강: line/equipment는 exact filter가 맞으면 우선 적용합니다.
    dense_docs: list[dict[str, Any]] = []
    try:
        dense_docs = search_similar_documents(
            user_query=error_name,
            process=process,
            top_k=max(top_k, 10),
            line=(line_aliases[0] if line_aliases else ""),
            equip=equip_raw,
        )
    except Exception as error:
        print(f"[WARN] alert dense retrieval with line/equip filter failed: {error}")
        try:
            dense_docs = search_similar_documents(
                user_query=error_name,
                process=process,
                top_k=max(top_k, 10),
            )
        except Exception as dense_error:
            print(f"[WARN] alert dense retrieval failed: {dense_error}")
            dense_docs = []

    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for doc in results:
        merged[_doc_key(doc)] = doc

    for doc in dense_docs:
        key = _doc_key(doc)
        if key in merged:
            existing = merged[key]
            existing["similarity"] = doc.get("similarity")
            channels = list(existing.get("retrieval_channels", []) or [])
            if "alert-dense-error" not in channels:
                channels.append("alert-dense-error")
            existing["retrieval_channels"] = channels
        else:
            doc = dict(doc)
            doc["alert_score"] = float(doc.get("alert_score", 0.0) or 0.0) + 120.0
            doc["match_level"] = "에러명 유사"
            doc["retrieval_channels"] = ["alert-dense-error"]
            merged[key] = doc

    final_docs = list(merged.values())

    def date_key(doc: dict[str, Any]) -> datetime:
        val = doc.get("날짜")
        if isinstance(val, datetime):
            return val
        try:
            return datetime.fromisoformat(str(val)[:19])
        except Exception:
            return datetime.min

    def final_score(doc: dict[str, Any]) -> float:
        score = float(doc.get("alert_score", 0.0) or 0.0)
        if doc.get("similarity") is not None:
            score += max(0.0, 150.0 * (1.0 - float(doc.get("similarity") or 1.0)))
        level = str(doc.get("match_level") or "")
        if "동일 라인" in level:
            score += 200
        elif "동일 설비" in level:
            score += 150
        elif "동일 모델" in level:
            score += 50
        # 최신성 보정: 최근 날짜가 위로 오도록 약하게 보정합니다.
        dt = date_key(doc)
        if dt != datetime.min:
            age_days = max(0, (datetime.now() - dt).days)
            score += max(0, 80 - min(age_days, 365) / 365 * 80)
        return score

    for doc in final_docs:
        doc["final_score"] = final_score(doc)

    final_docs.sort(key=lambda d: (float(d.get("final_score", 0.0) or 0.0), date_key(d)), reverse=True)
    return final_docs[: max(top_k, 1)]
