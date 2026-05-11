# PostgreSQL 완전 이전 구조

## 이번 버전에서 바뀐 점
이전 버전은 아래처럼 저장 계층이 나뉘어 있었습니다.

- 채팅 세션 / 메시지 원문: JSON 파일
- 대화 메모리: PostgreSQL

이번 버전은 채팅 세션과 메시지까지 PostgreSQL로 옮겨서 아래 구조로 통합했습니다.

- `chat_sessions`
- `chat_messages`
- `chat_session_memory`

즉, 채팅 관련 저장은 전부 PostgreSQL이 담당합니다.

## 생성되는 테이블
### 1. chat_sessions
채팅 세션 메타정보 저장

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    process TEXT NOT NULL DEFAULT 'MP',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 2. chat_messages
실제 사용자/assistant 메시지 저장

```sql
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 3. chat_session_memory
후속 질문용 구조화 메모리 저장

```sql
CREATE TABLE IF NOT EXISTS chat_session_memory (
    session_id TEXT PRIMARY KEY REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    process TEXT,
    current_line TEXT,
    current_equip TEXT,
    current_error TEXT,
    last_reference_doc_id TEXT,
    last_reference_summary TEXT,
    last_intent TEXT,
    last_symptom_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
    full_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## 기대 효과
### 1. 답변 속도
답변 생성 자체는 대부분 LLM 호출과 retrieval 시간이 지배합니다.
따라서 **모델 생성 속도가 극적으로 빨라지지는 않습니다.**

하지만 아래는 개선될 수 있습니다.

- 최근 메시지 조회가 파일 전체 read/write보다 안정적
- 긴 채팅에서도 특정 세션의 최근 메시지만 빠르게 조회 가능
- 여러 사용자가 동시에 써도 파일 충돌 없이 일관된 접근 가능

즉, 체감상 **대화가 길어질수록 더 안정적이고 부드럽게 동작할 가능성**이 큽니다.

### 2. 정합성
정합성 측면에서는 이점이 더 분명합니다.

- 최근 메시지를 정확한 순서대로 조회
- 세션별 구조화 메모리를 안정적으로 유지
- 직전 설비/라인/에러/증상 키워드를 더 신뢰성 있게 이어받음

즉 **후속 질문 처리 정확도와 맥락 유지 품질**에 더 유리합니다.

## 자동 마이그레이션
앱 시작 시 PostgreSQL 테이블이 준비되고,
만약 `backend/data/chat_sessions.json`이 존재하고 PostgreSQL `chat_sessions`가 비어 있으면
JSON 데이터를 PostgreSQL로 한 번 자동 이관합니다.

이 과정에서:
- 세션
- 메시지
- 기존 agentic_state 기반 memory

를 함께 옮깁니다.

## 현재 동작 흐름
1. 새 채팅 생성 → `chat_sessions` insert
2. 질문 전송 → `chat_messages`에 user 메시지 insert
3. 최근 메시지와 memory 조회
4. RAG 답변 생성
5. assistant 메시지 insert
6. `chat_session_memory` upsert
7. 세션 title / updated_at 갱신

## 왜 더 좋은가
이제는 chat logic 전체가 PostgreSQL 안에서 정리되므로,
향후 아래 확장이 쉬워집니다.

- 사용자별 사용량 분석
- 세션 검색
- 특정 키워드 포함 대화 찾기
- retrieval log 테이블 추가
- 대화 memory 리셋 API
- 오래된 세션 archive

## 주의점
이전보다 PostgreSQL 의존성이 더 커집니다.
즉 DB가 내려가면 채팅도 영향받습니다.
따라서 운영 환경에서는 PostgreSQL 연결과 백업이 더 중요해집니다.
