# backend 파일 설명서

이 문서는 `backend` 폴더에 있는 주요 파일의 역할을 파일 단위로 정리한 문서입니다.

## 루트 파일

### `backend/requirements.txt`
FastAPI 서버 실행에 필요한 파이썬 패키지 목록입니다.

- `fastapi`, `uvicorn`: API 서버 실행
- `python-dotenv`: `.env` 로드
- `pydantic`: 요청/응답 스키마 검증
- `langgraph`, `langchain-*`, `ollama`: LLM 및 그래프 로직
- `psycopg2-binary`: PostgreSQL 연결

### `backend/.env.example`
실제 `.env`를 만들 때 참고하는 샘플 파일입니다.

주요 키:
- `APP_*`: FastAPI 서버 정보
- `FRONTEND_ORIGIN`: CORS 허용 프런트 주소
- `ADMIN_*`: 로그인용 관리자 계정
- `LLM_MODEL`, `EMBEDDING_MODEL`, `PG_CONN_STR`: LLM / DB 연결 정보

### `backend/README.md`
백엔드 전환본의 목적과 실행 방법을 간단히 적어 둔 안내 문서입니다.

---

## 앱 진입점

### `backend/app/main.py`
FastAPI 앱의 시작점입니다.

역할:
- `FastAPI(title=...)` 생성
- CORS 미들웨어 등록
- 인증 / 채팅 / 인폼노트 라우터 연결
- `/api/health` 상태 확인 엔드포인트 제공

이 파일은 “서버 뼈대 조립 파일”로 이해하시면 됩니다.

### `backend/app/__init__.py`
패키지 인식을 위한 빈 초기화 파일입니다.

---

## core 폴더

### `backend/app/core/config.py`
환경 변수를 읽어 `Settings` 객체로 묶는 파일입니다.

역할:
- `.env` 자동 로드
- 앱 이름, 포트, 프런트 주소, 관리자 계정, 세션 만료 시간 보관
- 전역 `settings` 객체 제공

수정이 자주 필요한 값:
- `FRONTEND_ORIGIN`
- `ADMIN_USERNAME`, `ADMIN_PASSWORD`
- `SESSION_EXPIRE_HOURS`

### `backend/app/core/auth.py`
현재 1차 전환본의 인증 핵심 파일입니다.

역할:
- `SessionManager`로 메모리 기반 토큰 세션 관리
- 로그인 성공 시 토큰 생성
- Authorization 헤더에서 Bearer 토큰 파싱
- 현재 사용자 조회 의존성 함수 제공

핵심 함수:
- `session_manager.create(...)`
- `get_current_user(...)`
- `get_current_token(...)`

주의:
- 서버 재시작 시 세션이 사라집니다.
- 아직 DB 세션, JWT, refresh token 구조는 아닙니다.

### `backend/app/core/json_store.py`
간단한 JSON 파일 저장소 유틸입니다.

역할:
- `backend/data/` 폴더 자동 생성
- `chat_sessions.json` 로드/저장
- Lock을 사용해 동시 접근 충돌 완화

현재는 채팅 세션 저장만 맡고 있습니다.

### `backend/app/core/__init__.py`
패키지 인식을 위한 빈 파일입니다.

---

## routers 폴더

### `backend/app/routers/auth.py`
인증 관련 API 라우터입니다.

엔드포인트:
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/auth/logout`

흐름:
1. 로그인 요청 수신
2. `.env` 관리자 계정과 비교
3. 메모리 세션 토큰 발급
4. 프런트에 토큰 반환

### `backend/app/routers/chat.py`
채팅 세션 및 메시지 전송 API 라우터입니다.

엔드포인트:
- `GET /api/chat/sessions`
- `POST /api/chat/sessions`
- `GET /api/chat/sessions/{session_id}`
- `POST /api/chat/sessions/{session_id}/messages`

실제 비즈니스 로직은 `chat_service.py`로 넘깁니다.

### `backend/app/routers/inform.py`
인폼노트 DB 조회용 API 라우터입니다.

엔드포인트:
- `GET /api/inform/records`

쿼리 파라미터:
- `process`
- `line`
- `equip`
- `period`
- `start`
- `end`

실제 조회는 `inform_service.py`가 담당합니다.

### `backend/app/routers/__init__.py`
패키지 인식을 위한 빈 파일입니다.

---

## schemas 폴더

### `backend/app/schemas/auth.py`
로그인 요청/응답 구조를 정의합니다.

- `LoginRequest`
- `LoginResponse`

### `backend/app/schemas/chat.py`
채팅 관련 Pydantic 스키마 모음입니다.

주요 모델:
- `ChatMessage`
- `ChatSessionSummary`
- `ChatSessionDetail`
- `CreateChatSessionRequest`
- `SendMessageRequest`
- `SendMessageResponse`

프런트와 백엔드가 주고받는 JSON 구조 기준점 역할을 합니다.

### `backend/app/schemas/inform.py`
인폼노트 응답 구조를 정의합니다.

주요 모델:
- `InformRecord`
- `InformListResponse`

### `backend/app/schemas/__init__.py`
패키지 인식을 위한 빈 파일입니다.

---

## services 폴더

### `backend/app/services/chat_service.py`
채팅 기능의 핵심 서비스 파일입니다.

역할:
- 채팅 세션 목록 조회
- 새 채팅 세션 생성
- 특정 세션 조회
- 사용자 메시지 저장
- LangGraph 기반 답변 생성 호출
- 답변을 세션 메시지에 추가 저장

핵심 포인트:
- JSON 저장소 기반으로 세션을 유지합니다.
- `agentic_state`를 세션에 함께 저장해 다음 턴에 이어갈 수 있게 해 둡니다.
- 문서 번호 선택 단계에서는 `handle_doc_confirm()` 경로를 사용합니다.

### `backend/app/services/inform_service.py`
인폼노트 DB 조회 전용 서비스입니다.

역할:
- 공정명에 맞는 테이블 선택
- PostgreSQL 조회
- 라인/설비/기간 필터링
- `에러명` 기준 중복 수 계산
- 최신 이력 기준으로 unique row 반환

프런트 테이블에 바로 넣기 쉬운 형태로 데이터를 가공합니다.

### `backend/app/services/__init__.py`
패키지 인식을 위한 빈 파일입니다.

---

## legacy 폴더

이 폴더는 기존 Django/LangGraph 자산을 최대한 재사용하기 위해 가져온 파일들입니다.

### `backend/app/services/legacy/pg_vector_utils.py`
pgvector 검색 유틸입니다.

역할:
- Ollama embedding 생성
- 공정별 Inform 테이블 매핑
- 유사 문서 검색
- 메타데이터 필터(라인, 설비명 등) 지원

핵심 함수:
- `embed_query()`
- `search_similar_documents()`
- `hybrid_search_similar_documents()`

### `backend/app/services/legacy/agentic_rag_graph.py`
현재 챗봇 답변 로직의 핵심입니다.

역할:
- 질문을 `inform` / `general`로 분류
- inform 질문이면 pgvector 검색 수행
- 상위 문서 목록을 표 형태로 제시
- 사용자가 문서 번호를 선택하면 최종 답변 생성
- general 질문이면 일반 답변 생성
- LangGraph 상태 흐름으로 단계 전환 관리

핵심 단계 예시:
- `classify_question`
- `handle_general`
- `rag_retrieve`
- `handle_doc_confirm`
- `generate_final_answer`

### `backend/app/services/legacy/logging_utils.py`
간단한 CSV 로그 저장 유틸입니다.

역할:
- 질문
- 답변 일부
- 연관 에러명 목록
- 기록 시간

을 `chat_log.csv`에 저장합니다.

### `backend/app/services/legacy/__init__.py`
패키지 인식을 위한 빈 파일입니다.

---

## 앞으로 구조를 더 좋게 바꾸려면

1. `auth.py`를 JWT 또는 DB 세션 기반으로 확장
2. `json_store.py` 채팅 저장을 PostgreSQL로 이동
3. `legacy/agentic_rag_graph.py` 내부 프롬프트와 상태 로직을 단계별 파일로 분리
4. `inform_service.py` SQL을 repository 계층으로 분리
5. 에러 처리용 공통 exception handler 추가
