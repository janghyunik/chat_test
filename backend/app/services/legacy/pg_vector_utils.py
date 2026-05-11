import os
import re
from collections import defaultdict
from typing import Any, Iterable

import ollama
import psycopg2
from dotenv import load_dotenv

load_dotenv()

PG_CONN_STR = os.getenv("PG_CONN_STR") or "host=localhost dbname=vectorDB user=postgres password=1541"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "granite-embedding:278m"

PROCESS_TO_TABLE = {
    "MP": "inform_embedding_mp",
    "DA": "inform_embedding_da",
    "SMT": "inform_embedding_smt",
}

KEYWORD_STOPWORDS = {
    "이력", "오류", "에러", "알람", "점검", "점검이력", "조치", "발생", "현상",
    "관련", "내용", "알려줘", "알려주세요", "정리", "확인", "문의", "문제",
    "호기", "설비", "장비", "라인", "원인", "무엇", "뭐야", "조회", "요약",
    "경우", "대한", "기반", "please", "what", "show", "with", "from",
}


def _get_table_name(process: str) -> str:
    process = (process or "").strip().upper()
    if process not in PROCESS_TO_TABLE:
        raise ValueError(f"❌ 지원하지 않는 공정명: {process}")
    return PROCESS_TO_TABLE[process]


def _tokenize(text: str) -> list[str]:
    return [tok for tok in re.findall(r"[0-9A-Za-z가-힣]+", str(text or "").lower()) if tok]


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
    if line:
        filters.append('라인 = %s')
        meta_params.append(line)
    if equip:
        filters.append('설비명 = %s')
        meta_params.append(equip)
    if extra_filters:
        for key, value in extra_filters.items():
            if value in (None, ""):
                continue
            filters.append(f'{key} = %s')
            meta_params.append(value)

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
    kw_list = [kw for kw in (keywords or extract_query_keywords(user_query, limit=6)) if kw]
    if not kw_list:
        return []

    score_terms: list[str] = []
    score_params: list[Any] = []
    match_terms: list[str] = []
    match_params: list[Any] = []

    for kw in kw_list:
        pattern = f"%{kw}%"
        score_terms.append(
            "(" \
            "CASE WHEN 설비명 ILIKE %s THEN 4 ELSE 0 END + "
            "CASE WHEN 에러명 ILIKE %s THEN 8 ELSE 0 END + "
            "CASE WHEN 라인 ILIKE %s THEN 2 ELSE 0 END + "
            "CASE WHEN 점검이력 ILIKE %s THEN 3 ELSE 0 END + "
            "CASE WHEN text ILIKE %s THEN 2 ELSE 0 END" \
            ")"
        )
        score_params.extend([pattern, pattern, pattern, pattern, pattern])

        match_terms.append(
            "(" \
            "설비명 ILIKE %s OR 에러명 ILIKE %s OR 라인 ILIKE %s OR 점검이력 ILIKE %s OR text ILIKE %s" \
            ")"
        )
        match_params.extend([pattern, pattern, pattern, pattern, pattern])

    filters: list[str] = [f"({' OR '.join(match_terms)})"]
    meta_params: list[Any] = []
    if line:
        filters.append('라인 = %s')
        meta_params.append(line)
    if equip:
        filters.append('설비명 = %s')
        meta_params.append(equip)
    if extra_filters:
        for key, value in extra_filters.items():
            if value in (None, ""):
                continue
            filters.append(f'{key} = %s')
            meta_params.append(value)

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
) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}

    unique_queries: list[str] = []
    seen = set()
    for query in query_variants:
        query = (query or "").strip()
        if not query or query in seen:
            continue
        seen.add(query)
        unique_queries.append(query)

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

        try:
            keyword_docs = search_keyword_documents(
                user_query=query,
                process=process,
                top_k=keyword_top_k,
                line=line,
                equip=equip,
                extra_filters=extra_filters,
                keywords=extract_query_keywords(query, limit=6),
            )
        except Exception as error:
            print(f"[WARN] keyword retrieval failed for query={query!r}: {error}")
            keyword_docs = []

        for doc in keyword_docs:
            key = _doc_key(doc)
            if key not in merged:
                merged[key] = dict(doc)
                merged[key]["retrieval_count"] = 0
            merged[key] = _merge_doc(merged[key], doc, "keyword", query)

    results = list(merged.values())
    results.sort(
        key=lambda item: (
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
