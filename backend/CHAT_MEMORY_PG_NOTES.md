# PostgreSQL 대화 메모리 저장 구조

## 개요
이 버전부터 채팅 원문은 기존처럼 `backend/data/chat_sessions.json`에 저장하고,
대화 메모리(후속 질문 판단에 필요한 구조화 상태)는 PostgreSQL의 `chat_session_memory` 테이블에 별도로 저장합니다.

즉 저장 계층을 두 층으로 나눕니다.

- 원문 대화/세션: JSON 파일
- 구조화 메모리: PostgreSQL

이렇게 나누면 채팅 UI는 기존 구조를 유지하면서도,
후속 질문용 핵심 상태를 더 안정적으로 읽고 쓸 수 있습니다.

## 생성되는 테이블
애플리케이션 시작 시 아래 테이블이 자동 생성됩니다.

```sql
CREATE TABLE IF NOT EXISTS chat_session_memory (
    session_id TEXT PRIMARY KEY,
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

추가 인덱스도 같이 생성됩니다.

```sql
CREATE INDEX IF NOT EXISTS idx_chat_session_memory_owner_id ON chat_session_memory(owner_id);
CREATE INDEX IF NOT EXISTS idx_chat_session_memory_updated_at ON chat_session_memory(updated_at DESC);
```

## 각 컬럼 설명
- `session_id`
  - 채팅 세션 ID
  - JSON 저장소의 세션 ID와 동일
- `owner_id`
  - `admin:visitor_id` 형태
  - 같은 admin 계정이어도 브라우저별 채팅 분리 가능
- `process`
  - 현재 공정
- `current_line`
  - 직전 맥락에서 이어받은 라인
- `current_equip`
  - 직전 맥락에서 이어받은 설비명
- `current_error`
  - 직전 맥락에서 이어받은 에러명
- `last_reference_doc_id`
  - 직전 답변에서 가장 핵심 근거였던 문서 ID
- `last_reference_summary`
  - 직전 참고 문서 요약
- `last_intent`
  - 직전 질문 유형(예: summary, cause, action)
- `last_symptom_keywords`
  - 후속 질문에서 재사용할 증상 키워드 목록
- `recent_questions`
  - 최근 사용자 질문 목록
- `recent_answers`
  - 최근 답변 요약 목록
- `full_state`
  - 전체 agentic state JSON 스냅샷
- `created_at`, `updated_at`
  - 생성/수정 시각

## 동작 흐름
### 1. 새 채팅 생성
`create_session()` 호출 시:
- JSON 세션 생성
- PostgreSQL `chat_session_memory` 초기 row 생성

### 2. 질문 전송
`send_user_message()` 호출 시:
- 먼저 PostgreSQL에서 `chat_session_memory`를 읽음
- 그 state를 `answer_question_direct()`의 `previous_state`로 전달
- 답변 생성 후 갱신된 state를 다시 PostgreSQL에 upsert

### 3. 채팅 삭제
`delete_session()` 호출 시:
- JSON 세션 삭제
- PostgreSQL `chat_session_memory`도 같이 삭제

## 왜 좋은가
### 1. 후속 질문 정확도 향상
이전에는 세션 JSON 내부 상태에만 의존했지만,
이제는 구조화된 메모리를 별도로 읽어오므로 후속 질문 판단에 필요한 값들을 더 안정적으로 재사용할 수 있습니다.

### 2. 긴 대화에서 맥락 오염 감소
이전 대화 전체를 다시 해석하지 않고,
이미 저장된 `current_line`, `current_equip`, `current_error`, `last_symptom_keywords`만 바로 읽으면 되므로
긴 대화에서도 맥락 이어받기가 더 안정적입니다.

### 3. 운영/디버깅에 유리
나중에 실제 운영 시,
특정 세션이 왜 잘못 판단됐는지 테이블만 봐도 확인하기 쉬워집니다.

예:
- 어떤 설비명을 기억하고 있었는지
- 어떤 증상 키워드를 들고 있었는지
- 직전 질문 유형이 무엇이었는지

### 4. 향후 확장에 유리
나중에는 아래처럼 확장하기 좋습니다.

- `chat_messages` 테이블로 원문 대화도 DB화
- `chat_retrieval_log` 테이블 추가
- 사용자별 memory 분석
- 메모리 TTL/초기화 기능
- 세션 복구 기능

## 현재 버전의 한계
현재는 원문 대화는 여전히 JSON에 저장됩니다.
즉 완전한 DB 전환은 아니고,
`대화 메모리`만 PostgreSQL로 분리한 버전입니다.

원하시면 다음 단계에서는 아래까지 확장 가능합니다.

- `chat_sessions` / `chat_messages`도 PostgreSQL로 이전
- retrieval log까지 PostgreSQL에 저장
- 특정 세션 memory 수동 초기화 API 추가
- memory 이력 버전 관리
