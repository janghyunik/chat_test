# Phase25 - 실제 설비/라인/에러 표현 기반 RAG 검색 로직 최적화

## 목적

이번 단계는 Step1 컬럼 우선 검색을 실제 인폼노트 데이터 표현에 맞게 보강한 버전입니다.
DB 테이블 구조는 변경하지 않고, 기존 `inform_embedding_mp` 테이블의 컬럼만 사용합니다.

기존 문제는 다음과 같습니다.

- `embedding`이 `설비명 + 에러명 + 점검이력`을 합친 text 기준이라, 질문에 라인명/설비명이 빠지면 에러명 매칭이 약해질 수 있음
- `1L`, `1라인`, `1line` 같은 라인 표현이 서로 다르게 해석될 수 있음
- `MTR-2L02A`, `ATPS-1`, `E1-4L15`, `PT-4L02` 같은 설비명이 모델/라인/호기로 나뉘는데 기존 로직은 이를 충분히 활용하지 못함
- 에러명이 단순한 에러명뿐 아니라 정비사항, 알람, 코드, 영문/한글 혼합 표현까지 포함하므로 단일 token 검색으로는 부족함

## 주요 변경 파일

```text
backend/app/services/legacy/pg_vector_utils.py
backend/app/services/legacy/agentic_rag_graph.py
```

## 핵심 개선 내용

### 1. 라인 표현 확장

아래 표현을 같은 의미로 확장해 검색합니다.

```text
1, 1L, 1라인, 1line
2, 2L, 2라인, 2line
3, 3L, 3라인, 3line
4, 4L, 4라인, 4line
C5, C5라인, C5line
```

또한 설비명 안에 `2L`, `4L`처럼 라인 코드가 들어간 경우도 보조 점수로 반영합니다.

예:

```text
MTR-2L02A → 라인 후보 2
E1-4L15  → 라인 후보 4
PT-4L02  → 라인 후보 4
```

### 2. 설비명 패턴 분해

아래 같은 설비명을 `full`, `model`, `suffix`, `unit`, `line_from_equip`로 분리합니다.

```text
MTR-2L02A
ATPS-1
E1-4L15
PT-4L02
ATPS-1호기
```

예:

```text
MTR-2L02A
- full: MTR-2L02A, MTR2L02A
- model: MTR
- suffix: 2L02A
- line_from_equip: 2
- unit: 02A
```

질문에 `ATPS-1호기` 또는 `ATPS-1`이 들어오면 `ATPS-1`을 full 설비명 후보로 보고 강하게 매칭합니다.

### 3. 에러명/정비사항 phrase 검색 강화

에러명 예시가 아래처럼 복합 표현이기 때문에:

```text
봉함기 배출부 ERR
공급중 튐 추가 발생
(X0306) 라벨 PICKER 진공 ERROR
PKG이탈
INLET 삼성 라벨 파트 스캔 에러
커버테잎 밀림
REEL CHANGE-B 소재 흡착시간 초과 이상
JOB SETUP
LOADER TRAY CHECK FAIL ALARM
[TRAYBOXINGMODULE]소박스 도착 실패
```

단일 단어뿐 아니라 2~4개 토큰 phrase를 만들어 에러명/점검이력 컬럼에 검색합니다.

예:

```text
부자재 공급 에러 이력 알려줘
→ 부자재 공급
→ 공급 에러
→ 부자재 공급 에러
```

```text
라벨 PICKER 진공 ERROR 이력
→ 라벨 picker
→ picker 진공
→ 진공 error
→ 라벨 picker 진공 error
```

### 4. 컬럼 우선 검색 점수 강화

검색 우선순위는 다음과 같습니다.

```text
1. 라인/설비명/에러명/점검이력 컬럼 직접 매칭
2. keyword 검색
3. vector 검색
4. 최종 reranking
```

특히 에러명 컬럼에 직접 매칭되는 값은 vector 유사도보다 훨씬 높은 점수를 받습니다.

### 5. 최종 재랭킹 보강

최종 점수는 아래 요소를 합산합니다.

```text
structured_score      # 컬럼 우선 검색 점수
keyword_score         # keyword 검색 점수
vector_score          # embedding 유사도 점수
line_match_score      # 라인 표현 매칭
equipment_score       # 설비 full/model/unit 매칭
error_score           # 에러명/코드/phrase 매칭
history_score         # 점검이력 키워드 매칭
keyword_coverage      # 질문 키워드 coverage
recent_score          # 최신성
mismatch_penalty      # 명시 조건 불일치 감점
```

### 6. 명시 조건 불일치 감점 강화

질문에 명시된 조건이 후보 문서와 맞지 않으면 감점합니다.

예:

```text
ATPS-1L02 부자재 공급 에러 이력 알려줘
```

후보 문서의 설비명이 `ATPS-1L02` 계열이 아니면 큰 감점을 받습니다.

```text
3라인 커버테잎 밀림 이력 알려줘
```

후보 문서가 3라인과 맞지 않으면 감점을 받습니다.

```text
X0306 라벨 PICKER 진공 ERROR 이력 알려줘
```

후보 문서의 에러명/점검이력/text에 `X0306`이 없으면 큰 감점을 받습니다.

## 기대 효과

아래 질문 유형의 정합성 개선을 목표로 합니다.

```text
ATPS-1L02 부자재 공급 에러 이력 알려줘
ATPS-1호기 부자재 공급 에러 이력 알려줘
1L ATPS-1 공급중 튐 추가 발생 이력 알려줘
X0306 라벨 PICKER 진공 ERROR 이력 알려줘
커버테잎 밀림 이력 알려줘
REEL CHANGE-B 소재 흡착시간 초과 이상 이력 알려줘
LOADER TRAY CHECK FAIL ALARM 이력 알려줘
```

## 아직 남은 한계

이번 버전은 DB 구조를 변경하지 않는 Step1 보강입니다.
따라서 아래 한계는 남아 있습니다.

- `ILIKE '%키워드%'` 검색이 많아 데이터가 커지면 느려질 수 있음
- 동의어/약어 사전은 아직 별도 테이블로 관리하지 않음
- `error_embedding`, `history_embedding`처럼 embedding 컬럼을 세분화한 구조는 아직 적용하지 않음

다음 단계에서는 아래를 권장합니다.

```text
1. error_norm, equip_norm, history_norm 정규화 컬럼 추가
2. pg_trgm 인덱스 추가
3. error_alias / equipment_alias 사전 테이블 추가
4. error_embedding, history_embedding, error_history_embedding 등 multi-vector 구조 도입
5. 질문-정답 평가셋 생성 후 Top-1/Top-3 hit rate 측정
```
