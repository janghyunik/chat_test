# 챗봇&인폼노트DB 웹 모니터링 데이터 수집 구조

이번 버전은 별도 모니터링 웹에서 조회할 수 있도록 PostgreSQL에 운영 지표를 저장합니다.

## 추가 테이블

### monitoring_visitor_sessions
브라우저별 현재 접속 상태를 저장합니다.

- visitor_id: 브라우저별 식별자
- owner_id: username:visitor_id
- username: 현재는 admin
- ip_address: 접속 IP
- user_agent: 브라우저 정보
- first_seen_at: 최초 접속 시각
- last_seen_at: 마지막 heartbeat 시각
- last_path: 마지막 페이지 경로
- heartbeat_count: heartbeat 횟수

### monitoring_events
API 요청, heartbeat 같은 이벤트를 시간순으로 저장합니다.

- event_type: api_request, heartbeat 등
- path, method, status_code
- duration_ms: API 응답 시간
- active_requests: 해당 요청 시점의 동시 처리 요청 수
- extra: query string 등 보조 정보

### monitoring_chat_interactions
질문/답변 단위 성능과 retrieval 정보를 저장합니다.

- question / question_normalized
- answer_chars
- reference_doc_count
- intent / is_follow_up
- candidate_count_first / second / final
- first_token_ms
- total_duration_ms
- retrieval_debug
- status / error_message

## 별도 모니터링 웹에서 확인 가능한 것

- 현재 활성 접속자 수
- 최근 24시간 접속자 수
- 질문 수
- 많이 물어본 질문
- 평균 답변 시간 / p95 답변 시간
- API 응답 시간
- 시간대별 요청량과 부하
- 활성 접속자 수가 늘어날 때 답변 속도가 느려지는 구간

## 주의

모니터링 테이블은 계속 증가합니다. 운영에서는 30일 또는 90일 보관 정책을 권장합니다.
