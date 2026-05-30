# Phase23 - 채팅 처리 프로세스 표시 기능

이번 버전은 사용자가 질문을 보낸 뒤 답변이 생성되기 전/중에 백엔드에서 실제로 어떤 RAG 처리 단계가 진행되고 있는지 채팅 UI에 표시하도록 개선한 버전입니다.

## 변경 요약

기존 동작:

1. 질문 전송
2. `문서를 확인하고 응답을 정리하고 있습니다` 문구만 표시
3. RAG 검색/재랭킹/프롬프트 구성 완료 후 LLM 답변 스트리밍

변경 후:

1. 질문 접수
2. 질문 해석
3. 1차 후보 검색
4. 1차 재랭킹
5. 검색 초점 추정
6. 2차 보강 검색
7. 최종 재랭킹
8. 참조 문서 확정
9. 답변 프롬프트 구성
10. LLM 답변 생성
11. 참조 정보 정리

위 단계들이 채팅 말풍선 안에 타임라인 형태로 표시됩니다.

## 백엔드 변경

### `backend/app/services/legacy/agentic_rag_graph.py`

- `_process_event()` 추가
- `_prepare_answer_context_with_progress()` 추가
- 스트리밍 답변 함수 `answer_question_stream()`이 텍스트 delta뿐 아니라 process 이벤트도 yield하도록 변경
- direct 답변 함수는 기존처럼 `_prepare_answer_context()`를 사용하며, 내부적으로 progress generator를 drain합니다.

### `backend/app/services/chat_service.py`

- `answer_question_stream()`에서 넘어오는 dict 이벤트를 인식하도록 변경
- `event == process`인 경우 NDJSON `{ type: "process", ... }`로 프론트에 전달
- `event == delta` 또는 문자열인 경우 기존처럼 답변 텍스트 delta로 전달
- 기존 datetime 직렬화 fallback 유지

## 프론트엔드 변경

### `my-app/src/components/chat-screen.tsx`

- `ProcessStep` 타입 추가
- `ProcessTimeline` 컴포넌트 추가
- 스트리밍 payload의 `type: "process"` 이벤트 처리 추가
- pending assistant 말풍선에서 현재 처리 단계 표시

### `my-app/src/app/globals.css`

- `.process-timeline`
- `.process-step`
- `.process-dot`
- `.process-label`
- `.process-detail`
- `processPulse` 애니메이션

등 프로세스 표시 UI 스타일 추가

## 기대 효과

- 사용자가 질문 후 기다리는 동안 시스템이 멈춘 것처럼 보이지 않음
- 실제로 column-first 검색, embedding 검색, keyword 검색, reranking, 문서 확정, 답변 생성 중 어느 단계인지 확인 가능
- RAG 정합성 문제를 디버깅할 때도 어느 단계에서 오래 걸리는지 체감적으로 확인 가능

## 적용 방법

기존 `chat_test` 폴더에 덮어쓴 뒤 backend/frontend를 재시작하세요.

```bat
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bat
cd my-app
npm run dev
```
