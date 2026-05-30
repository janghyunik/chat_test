# Step 1 컬럼 우선 RAG 검색 개선 notes

## 목적

현재 `inform_embedding_*` 테이블 구조를 변경하지 않고, 기존 컬럼만 활용해 정확한 인폼노트 행을 더 잘 찾도록 검색 로직을 개선했습니다.

기존 로직은 `text = 설비명 + 에러명 + 점검이력`을 embedding한 벡터 검색 비중이 높았습니다. 이 경우 질문에 라인명/설비명이 없거나, 에러명 일부만 들어간 경우 정확한 행보다 의미적으로 가까운 다른 행이 상위에 올라올 수 있습니다.

이번 Step 1에서는 다음 우선순위를 적용했습니다.

1. 라인/설비명/에러명/점검이력 컬럼 기반 검색
2. 키워드 검색
3. 벡터 검색
4. 최종 reranking

즉, 명확한 DB 컬럼 값은 embedding보다 더 강하게 반영합니다.

---

## 수정 파일

- `backend/app/services/legacy/pg_vector_utils.py`
- `backend/app/services/legacy/agentic_rag_graph.py`

---

## 주요 변경 내용

### 1. `search_column_priority_documents()` 추가

현재 테이블을 그대로 사용하며 아래 컬럼에서 먼저 후보를 찾습니다.

- `라인`
- `설비명`
- `에러명`
- `점검이력`
- `text`

질문에서 추출된 토큰은 컬럼별로 다르게 가중치를 받습니다.

예시:

- 에러명 컬럼 매칭: 매우 높은 점수
- 설비명 컬럼 매칭: 높은 점수
- 점검이력 컬럼 매칭: 중간 점수
- text 컬럼 매칭: 보조 점수

특히 숫자 에러 코드가 질문에 있는 경우 `에러명`, `점검이력`, `text`에서 강하게 찾습니다.

---

### 2. hybrid retrieval 순서 변경

기존:

```text
vector search + keyword search
```

변경:

```text
column-first search + vector search + keyword search
```

컬럼 우선 검색 결과는 `retrieval_channels = ["column"]`로 표시됩니다.

---

### 3. reranking에서 컬럼 점수 강화

`agentic_rag_graph.py`의 reranking 점수에 다음 요소를 추가/강화했습니다.

- `structured_score`
- 라인 토큰 매칭
- 호기/설비 토큰 매칭
- 에러명 토큰 매칭
- 증상/점검이력 키워드 매칭
- 명시 토큰 불일치 감점

특히 사용자가 `582`처럼 에러 코드를 입력했는데 후보 문서에 해당 코드가 없으면 강하게 감점합니다.

---

## 기대 효과

아래 질문 유형에서 기존보다 정확한 행을 찾을 가능성이 높아집니다.

```text
정렬 오류 582 이력 알려줘
파워서플라이 이력 알려줘
센서 미감지 이력 알려줘
3라인 정렬 오류 이력 알려줘
스태커 1호기 정렬 오류 582 이력 알려줘
```

---

## 한계

이번 Step 1은 DB 구조를 바꾸지 않는 개선입니다. 따라서 다음 한계는 남아 있습니다.

- `ILIKE` 기반 검색이라 데이터가 매우 커지면 느려질 수 있음
- 한국어 형태소 분석은 정교하지 않음
- 에러명 유사어/약어 처리는 제한적임
- 같은 의미지만 표현이 다른 점검이력 매칭은 embedding 의존도가 여전히 있음

---

## 다음 단계 권장

Step 1 결과를 테스트한 뒤 정합성이 아직 부족하면 다음 순서를 권장합니다.

1. `pg_trgm` 인덱스 추가
2. `error_norm`, `equip_norm`, `history_norm` 정규화 컬럼 추가
3. `error_embedding`, `history_embedding`, `error_history_embedding` 분리 embedding 추가
4. 질문-정답 평가셋 30~50개 작성 후 Top-1/Top-3 hit 평가

