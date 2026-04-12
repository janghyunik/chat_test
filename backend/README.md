# backend 1차 전환본

이번 단계에서는 아래만 남겼습니다.

- 로그인 API
- ChatGPT 스타일의 채팅 세션/이력 API
- InformNote DB 조회 API
- 기존 LangGraph 기반 agentic_rag_graph 재사용

## 실행

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
