# chat_test 1차 전환본 개요

이 문서는 현재 `chat_test_phase1_scaffold` 기준 프로젝트 구조와 역할을 빠르게 이해하기 위한 입문 문서입니다.

## 현재 남겨 둔 기능

- FastAPI 기반 로그인 API
- ChatGPT 스타일 메인 채팅 화면
- 채팅 세션 생성 / 조회 / 이전 대화 목록
- 인폼노트 DB 조회 화면
- 기존 Django 프로젝트에서 가져온 LangGraph + pgvector 기반 답변 로직 재사용

## 현재 제거한 기능

- Morning Brief
- 바로가기 카드
- MTBI DB 탭
- PKG Assistant 탭
- 기존 Django 템플릿 기반 메인 대시보드

## 폴더 구조

```text
chat_test/
├─ backend/      # FastAPI 서버
├─ my-app/       # Next.js 프런트엔드
└─ docs/         # 이번 설명 문서 모음
```

## 데이터 흐름

1. 사용자가 Next.js 화면에서 로그인합니다.
2. 프런트는 `/api/auth/login`에 로그인 요청을 보냅니다.
3. FastAPI는 메모리 세션 토큰을 발급합니다.
4. 프런트는 쿠키에 토큰을 저장합니다.
5. 이후 채팅 세션 조회, 질문 전송, 인폼노트 조회 요청마다 `Authorization: Bearer ...` 헤더를 붙입니다.
6. FastAPI는 인증 확인 후 JSON 응답을 반환합니다.

## 지금 단계에서 꼭 알아둘 점

- 세션은 **메모리 기반**이라 서버 재시작 시 로그인이 풀립니다.
- 채팅 이력은 `backend/data/chat_sessions.json`에 저장됩니다.
- 인폼노트 조회는 PostgreSQL과 pgvector 환경이 준비되어 있어야 정상 동작합니다.
- 실제 답변 품질은 `backend/app/services/legacy/agentic_rag_graph.py`에 크게 의존합니다.

## 어디부터 읽으면 좋은가

### 백엔드 처음 볼 때
1. `backend/app/main.py`
2. `backend/app/routers/*.py`
3. `backend/app/services/chat_service.py`
4. `backend/app/services/inform_service.py`
5. `backend/app/services/legacy/*.py`

### 프런트 처음 볼 때
1. `my-app/src/app/page.tsx`
2. `my-app/src/components/chat-screen.tsx`
3. `my-app/src/components/app-sidebar.tsx`
4. `my-app/src/lib/api.ts`
5. `my-app/src/app/globals.css`
