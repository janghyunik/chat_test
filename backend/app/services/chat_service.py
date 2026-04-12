from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.core.json_store import CHAT_STORE_PATH, load_json, save_json
from app.services.legacy.agentic_rag_graph import (
    agentic_rag_graph,
    generate_final_answer,
    handle_doc_confirm,
)


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


def _build_initial_agentic_state(question: str, process: str) -> dict[str, Any]:
    return {
        "user_question": question,
        "current_step": "classify_question",
        "mode": "",
        "metadata": {},
        "meta_confirmed": None,
        "user_message": question,
        "docs": [],
        "selected_doc": None,
        "llm_prompt": "",
        "llm_response": "",
        "process": process,
        "retry_count": 0,
        "next_step": None,
    }


def send_user_message(username: str, session_id: str, content: str) -> tuple[dict[str, Any], str]:
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="질문이 비어 있습니다.")

    session = get_session(username, session_id)
    process = session.get("process", "MP")
    previous_state = deepcopy(session.get("agentic_state") or {})
    current_step = str(previous_state.get("current_step") or "").strip()

    user_message = {
        "id": uuid4().hex,
        "role": "user",
        "content": content,
        "created_at": _now(),
    }
    session["messages"].append(user_message)

    if current_step in ("wait_for_doc_choice", "wait_for_doc_confirm") and previous_state.get("docs"):
        previous_state["user_message"] = content
        previous_state["user_question"] = previous_state.get("user_question") or content
        next_state = handle_doc_confirm(previous_state)
        if next_state.get("current_step") == "generate_final_answer":
            next_state = generate_final_answer(next_state)
    else:
        next_state = _build_initial_agentic_state(question=content, process=process)
        next_state = agentic_rag_graph.invoke(next_state)

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
