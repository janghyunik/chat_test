# Phase26: 검색 질의 수 제한 기반 1차 후보 검색 속도 개선

## 배경

이전 Step1/Phase25에서는 실제 설비명/라인명/에러명 표현을 더 잘 잡기 위해 질문에서 여러 검색 질의를 생성했습니다.
예를 들어 원 질문 외에도 설비명 조합, 에러명 중심 질의, 점검이력 중심 질의, keyword 질의 등을 만들었습니다.

이 방식은 recall을 넓히는 장점이 있지만, 각 질의마다 다음 작업이 반복될 수 있습니다.

- Ollama embedding 생성
- pgvector dense 검색
- PostgreSQL keyword/ILIKE 검색
- 후보 병합

따라서 질문 1회에 검색 질의가 8~10개까지 늘어나면 `1차 후보 검색` 단계가 가장 오래 걸릴 수 있습니다.

## 이번 변경 방향

검색 질의는 최대 2개만 사용하도록 제한했습니다.

1. 원 질문
2. 라인/설비명/에러명/증상 키워드를 압축한 compact query

예시:

```text
사용자 질문: ATPS-1L02 부자재 공급 에러 이력 알려줘
검색 질의 1: ATPS-1L02 부자재 공급 에러 이력 알려줘
검색 질의 2: ATPS-1L02 부자재 공급 에러
```

## 내부 검색 방식

`hybrid_search_similar_documents()`는 이제 다음 방식으로 동작합니다.

1. 컬럼 우선 검색: 최대 2개 질의를 합친 combined query로 1회 수행
2. dense/vector 검색: 최대 2개 질의에 대해서만 수행
3. keyword 검색: combined query로 1회만 수행
4. 후보 병합 후 재랭킹

즉 이전보다 반복 검색 횟수가 크게 줄어듭니다.

## 기대 효과

- 1차 후보 검색 시간 단축
- Ollama embedding 호출 횟수 감소
- PostgreSQL ILIKE 검색 반복 감소
- 기존 컬럼 우선 검색/재랭킹 로직은 유지

## 환경변수

`.env`에서 아래 값을 조정할 수 있습니다.

```env
RAG_MAX_QUERY_VARIANTS=2
```

현재 코드는 안정성을 위해 최대값을 2로 제한합니다.

## 수정 파일

```text
backend/app/services/legacy/agentic_rag_graph.py
backend/app/services/legacy/pg_vector_utils.py
backend/.env.example
```
