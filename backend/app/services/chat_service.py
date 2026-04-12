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
    store = load_json(CHAT_STORE_PATH, _default_store())
    store.setdefault("users", {})
    return store


def _save_store(store: dict[str, Any]) -> None:
    save_json(CHAT_STORE_PATH, store)


def _session_title_from_text(text: str) -> str:
    clean = " ".join((text or "새 채팅").split())
    return clean[:30] or "새 채팅"


def list_sessions(owner_id: str) -> list[dict[str, Any]]:
    """
    owner_id 예시: 'admin:visitor_uuid'
    같은 admin 계정으로 로그인해도 visitor_id가 다르면 서로 다른 채팅 목록을 보게 됩니다.
    """
    store = _load_store()
    sessions = store["users"].get(owner_id, [])
    return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)


def create_session(owner_id: str, title: str | None = None, process: str = "MP") -> dict[str, Any]:
    store = _load_store()
    store["users"].setdefault(owner_id, [])

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
    store["users"][owner_id].append(session)
    _save_store(store)
    return session


def delete_session(owner_id: str, session_id: str) -> None:
    store = _load_store()
    sessions = store["users"].get(owner_id, [])
    filtered = [session for session in sessions if session["id"] != session_id]
    if len(filtered) == len(sessions):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")

    store["users"][owner_id] = filtered
    _save_store(store)


def get_session(owner_id: str, session_id: str) -> dict[str, Any]:
    store = _load_store()
    sessions = store["users"].get(owner_id, [])
    for session in sessions:
        if session["id"] == session_id:
            return session
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")


def _update_session(owner_id: str, updated_session: dict[str, Any]) -> dict[str, Any]:
    store = _load_store()
    sessions = store["users"].setdefault(owner_id, [])
    for index, session in enumerate(sessions):
        if session["id"] == updated_session["id"]:
            sessions[index] = updated_session
            _save_store(store)
            return updated_session
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 저장할 수 없습니다.")


def send_user_message(owner_id: str, session_id: str, content: str) -> tuple[dict[str, Any], str]:
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="질문이 비어 있습니다.")

    session = get_session(owner_id, session_id)
    process = session.get("process", "MP")

    user_message = {
        "id": uuid4().hex,
        "role": "user",
        "content": content,
        "created_at": _now(),
    }
    session["messages"].append(user_message)

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

    updated = _update_session(owner_id, session)
    return updated, answer
