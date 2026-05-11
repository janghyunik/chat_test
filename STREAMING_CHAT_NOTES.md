# Streaming Chat Notes

이번 버전은 채팅 답변을 스트리밍 방식으로 바꾼 버전입니다.

## 핵심 동작
- 질문은 즉시 화면에 표시됩니다.
- 답변 텍스트는 스트리밍으로 점진적으로 표시됩니다.
- 참조 표/기본 정보 블록은 마지막에 한 번에 추가됩니다.

## 주의사항
- 첫 토큰이 나오기 전까지는 retrieval / reranking / 플레이북 정리 시간이 필요합니다.
- 따라서 "질문 직후 바로 한 글자씩" 시작되지 않을 수 있습니다.
- 사내망 프록시나 리버스 프록시가 응답을 버퍼링하면 스트리밍 체감이 줄어들 수 있습니다.
- 운영 환경에서 프록시를 쓴다면 response buffering 비활성화가 필요할 수 있습니다.

## 수정 파일
- backend/app/services/legacy/agentic_rag_graph.py
- backend/app/services/chat_service.py
- backend/app/routers/chat.py
- my-app/src/components/chat-screen.tsx
- my-app/src/lib/api.ts
- my-app/src/app/globals.css
