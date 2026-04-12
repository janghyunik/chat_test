# pg_vector_utils.py

import os
import psycopg2
import ollama
from dotenv import load_dotenv

# ✅ .env에서 DB 정보 로드
load_dotenv()

PG_CONN_STR = os.getenv("PG_CONN_STR") or "host=localhost dbname=vectorDB user=postgres password=1541"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL") or "granite-embedding:278m"

# ✅ INFORM 테이블 이름 매핑
PROCESS_TO_TABLE = {
    "MP": "inform_embedding_mp",
    "DA": "inform_embedding_da",
    "SMT": "inform_embedding_smt",
    # 필요시 추가
}

# ✅ MTBI 테이블 이름 매핑
MTBI_TO_TABLE = {
    "MP": "mtbi_embedding_mp",
    "DA": "mtbi_embedding_da",
    "SMT": "mtbi_embedding_smt",
    # 필요시 추가
}

def embed_query(query: str, model: str = EMBEDDING_MODEL):
    """Ollama를 통한 쿼리 임베딩 생성"""
    response = ollama.embeddings(model=model, prompt=query)
    return response['embedding']

def search_similar_documents(
    user_query: str,
    process: str,
    top_k: int = 10,
    line: str = "",
    equip: str = "",
    extra_filters: dict = None
):
    """
    pgvector를 통한 유사도 기반 문서 검색 + (선택적) 메타데이터 하이브리드 필터 지원
    - user_query: 에러명(주로) 또는 자유질문(임베딩)
    - process: 공정명 (MP/DA/SMT)
    - line, equip: 라인명/설비명 필터링 (AND 조건, 일부만 입력해도 필터)
    - extra_filters: 기타 (dict, key=DB컬럼명, value=값)
    """
    process = process.strip().upper()
    if process not in PROCESS_TO_TABLE:
        raise ValueError(f"❌ 지원하지 않는 공정명: {process}")

    table_name = PROCESS_TO_TABLE[process]
    query_vector = embed_query(user_query)

    print(f"[DEBUG] process → table: {process} → {table_name}")
    print(f"[DEBUG] embedding vector[:5]: {query_vector[:5]}... (len={len(query_vector)})")

    # SQL WHERE 필터 조건 및 파라미터 구성
    filters = []
    meta_params = []

    if line:
        filters.append("라인 = %s")
        meta_params.append(line)
    if equip:
        filters.append("설비명 = %s")
        meta_params.append(equip)
    if extra_filters:
        for k, v in extra_filters.items():
            filters.append(f"{k} = %s")
            meta_params.append(v)

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
    print(f"[DEBUG] WHERE 필터 조건: {where_clause}")

    # 최종 파라미터 순서: 벡터 → 메타값들 → 벡터 → top_k
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

    print(f"[DB] candidates found: {len(rows)}")
    results = []
    for row in rows:
        results.append({
            "id": row[0],
            "text": row[1],
            "날짜": row[2],
            "라인": row[3],
            "공정": row[4],
            "설비명": row[5],
            "에러명": row[6],
            "점검이력": row[7],
            "source": row[8],
            "similarity": row[9],
        })
    return results

def hybrid_search_similar_documents(
    error_name: str,
    process: str,
    line: str = "",
    equip: str = "",
    top_k: int = 10
):
    """
    하이브리드 검색을 명시적으로 호출하는 함수 (에러명 임베딩 + 라인명/설비명 filter)
    """
    return search_similar_documents(
        user_query=error_name,
        process=process,
        top_k=top_k,
        line=line,
        equip=equip
    )
