from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.core.json_store import CHAT_STORE_PATH, load_json, save_json
from app.services.legacy.agentic_rag_graph import answer_question_direct


def _default_store() -> dict[str, Any]:
    return {"users": {}}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_store() -> dict[str, Any]:
    return load_json(CHAT_STORE_PATH, _default_store())


def _save_store(store: dict[str, Any]) -> None:
    save_json(CHAT_STORE_PATH, store)


def _session_title_from_text(text: str) -> str:
    clean = " ".join((text or "새 채팅").split())
    return clean[:30] or "새 채팅"


def list_sessions(username: str) -> list[dict[str, Any]]:
    store = _load_store()
    sessions = store["users"].get(username, [])
    return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)


def create_session(username: str, title: str | None = None, process: str = "MP") -> dict[str, Any]:
    store = _load_store()
    store["users"].setdefault(username, [])

    now = _now()
    session = {
        "id": uuid4().hex,
        "title": title or "새 채팅",
        "created_at": now,
        "updated_at": now,
        "process": process.upper() or "MP",
        "messages": [],
        "agentic_state": {},
    }
    store["users"][username].append(session)
    _save_store(store)
    return session


def delete_session(username: str, session_id: str) -> None:
    store = _load_store()
    sessions = store["users"].get(username, [])
    filtered = [session for session in sessions if session["id"] != session_id]
    if len(filtered) == len(sessions):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")

    store["users"][username] = filtered
    _save_store(store)


def get_session(username: str, session_id: str) -> dict[str, Any]:
    store = _load_store()
    sessions = store["users"].get(username, [])
    for session in sessions:
        if session["id"] == session_id:
            return session
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")


def _update_session(username: str, updated_session: dict[str, Any]) -> dict[str, Any]:
    store = _load_store()
    sessions = store["users"].setdefault(username, [])
    for index, session in enumerate(sessions):
        if session["id"] == updated_session["id"]:
            sessions[index] = updated_session
            _save_store(store)
            return updated_session
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 저장할 수 없습니다.")


def send_user_message(username: str, session_id: str, content: str) -> tuple[dict[str, Any], str]:
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="질문이 비어 있습니다.")

    session = get_session(username, session_id)
    process = session.get("process", "MP")

    user_message = {
        "id": uuid4().hex,
        "role": "user",
        "content": content,
        "created_at": _now(),
    }
    session["messages"].append(user_message)

    # 기존의 "표를 보여주고 번호를 다시 입력받는" 단계를 제거하고,
    # 질문 즉시 유사 문서를 검색한 뒤 최종 답변까지 한 번에 생성합니다.
    next_state = answer_question_direct(question=content, process=process)

    answer = next_state.get("llm_response", "답변을 생성하지 못했습니다.")
    assistant_message = {
        "id": uuid4().hex,
        "role": "assistant",
        "content": answer,
        "created_at": _now(),
    }
    session["messages"].append(assistant_message)
    session["agentic_state"] = next_state
    session["updated_at"] = _now()
    if session["title"] == "새 채팅":
        session["title"] = _session_title_from_text(content)

    updated = _update_session(username, session)
    return updated, answer
