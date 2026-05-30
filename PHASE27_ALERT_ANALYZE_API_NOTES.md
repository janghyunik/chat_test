# Phase27 - 설비 에러 자동 분석 API 추가

## 목적

별도 에러 감지/메일링 서비스가 기존 챗봇&인폼노트DB 웹 서버의 RAG 분석 기능을 재사용할 수 있도록 분석 API를 추가했습니다.

신규 API:

```http
POST /api/alert/analyze-error
```

## 요청 예시

```json
{
  "line": "2",
  "equipment": "ATPS-1L02",
  "error_name": "부자재 공급 에러",
  "occurred_at": "2026-05-28 13:10:00",
  "process": "MP",
  "reference_doc_count": 5
}
```

## 응답 예시

```json
{
  "ok": true,
  "summary": "## 요약 ...",
  "recommended_actions": ["센서 감도 확인", "자재 공급 위치 확인"],
  "history_rows": [
    {
      "no": 1,
      "date": "2025-09-17",
      "line": "2",
      "equipment": "ATPS-1L02",
      "error_name": "부자재 공급 에러",
      "inspection": "Send wait pos 84 > 85로 수정"
    }
  ],
  "confidence": "중간",
  "elapsed_ms": 12345.67,
  "links": {
    "inform": "http://.../inform?...",
    "chat": "http://...?seed=..."
  }
}
```

## 수정 파일

- `backend/app/routers/alert.py` 신규
- `backend/app/services/alert_analysis_service.py` 신규
- `backend/app/main.py` 라우터 등록
- `backend/.env.example` `ALERT_API_KEY` 추가

## 보안

`.env`에 `ALERT_API_KEY`를 설정하면 신규 알림 서비스에서 API 호출 시 헤더를 포함해야 합니다.

```http
X-Alert-Api-Key: your-secret-key
```

테스트 단계에서는 비워둘 수 있지만, 운영에서는 반드시 설정하는 것을 권장합니다.

## 설계 의도

- 기존 채팅 세션을 만들지 않습니다.
- 기존 `answer_question_direct()` RAG 로직을 재사용합니다.
- 신규 알림 서비스는 이 API를 호출해 LLM 요약/이력표/링크를 받아 메일 본문을 생성합니다.
