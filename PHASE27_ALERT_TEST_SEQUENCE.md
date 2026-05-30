# Phase27 전체 테스트 순서

## 1. 기존 chat_test backend 실행

```bat
cd chat_test\backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 2. 분석 API 직접 테스트

```http
POST http://localhost:8000/api/alert/analyze-error
Content-Type: application/json

{
  "line": "2",
  "equipment": "ATPS-1L02",
  "error_name": "부자재 공급 에러",
  "process": "MP",
  "reference_doc_count": 5
}
```

정상이라면 `summary`, `recommended_actions`, `history_rows`가 반환됩니다.

## 3. error_alert_service 실행

```bat
cd error_alert_service_v1\backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8200
```

```bat
cd error_alert_service_v1\frontend
npm run dev
```

## 4. 구독자와 구독 룰 등록

- 구독자: 이름/이메일 등록
- 룰: 전체 수신 테스트는 조건을 비워서 등록

## 5. 테스트 이벤트 생성

frontend에서 line/equipment/error_name 입력 후 `테스트 이벤트 생성 후 즉시 처리`를 클릭합니다.

## 6. 확인

- alert status가 done인지 확인
- 분석 summary가 생성되었는지 확인
- delivery log가 mocked인지 확인
