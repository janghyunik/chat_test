from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime
from time import perf_counter

from fastapi import HTTPException, status

from app.services.chat_pg_store import (
    append_message,
    create_session,
    delete_session,
    get_recent_messages,
    get_session,
    get_session_memory,
    list_sessions,
    update_session_title,
    upsert_session_memory,
    update_session_reference_doc_count,
)
from app.services.legacy.agentic_rag_graph import answer_question_direct, answer_question_stream
from app.services.monitoring_store import insert_chat_interaction


def _to_ndjson(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o),
    ) + "\n"


def _session_title_from_text(text: str) -> str:
    clean = " ".join((text or "새 채팅").split())
    return clean[:30] or "새 채팅"


def _owner_parts(owner_id: str) -> tuple[str | None, str | None]:
    if ":" not in owner_id:
        return None, None
    username, visitor_id = owner_id.split(":", 1)
    return username or None, visitor_id or None


def _safe_retrieval_debug(state: dict | None) -> dict:
    if not state:
        return {}
    debug = dict(state.get("retrieval_debug", {}) or {})
    if "document_count" not in debug:
        docs = state.get("docs", []) or []
        debug["document_count"] = len(docs)
    return debug


def _log_chat_metric(
    *,
    owner_id: str,
    session_id: str,
    question: str,
    answer: str | None,
    user_message_id: str | None,
    assistant_message_id: str | None,
    reference_doc_count: int | None,
    state: dict | None,
    status: str,
    error_message: str | None = None,
    first_token_ms: float | None = None,
    total_duration_ms: float | None = None,
) -> None:
    try:
        username, visitor_id = _owner_parts(owner_id)
        insert_chat_interaction(
            owner_id=owner_id,
            visitor_id=visitor_id,
            username=username,
            session_id=session_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            question=question,
            answer=answer or "",
            reference_doc_count=reference_doc_count,
            retrieval_debug=_safe_retrieval_debug(state),
            status=status,
            error_message=error_message,
            first_token_ms=first_token_ms,
            total_duration_ms=total_duration_ms,
        )
    except Exception as error:
        print(f"[WARN] failed to save chat monitoring metric: {error}")


def send_user_message(owner_id: str, session_id: str, content: str, reference_doc_count: int | None = None) -> tuple[dict, str]:
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="질문이 비어 있습니다.")

    started = perf_counter()
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    next_state: dict | None = None

    session = get_session(owner_id, session_id)
    process = session.get("process", "MP")
    if reference_doc_count is not None:
        update_session_reference_doc_count(owner_id, session_id, reference_doc_count)
        session = get_session(owner_id, session_id)
    active_reference_doc_count = int(session.get("reference_doc_count") or 6)

    user_message = append_message(owner_id, session_id, "user", content)
    user_message_id = str(user_message.get("id") or "")

    previous_state = session.get("agentic_state") or {}
    try:
        pg_state = get_session_memory(owner_id, session_id, process)
        if pg_state:
            previous_state = pg_state
    except Exception as error:
        print(f"[WARN] failed to load postgres chat memory: {error}")

    try:
        next_state = answer_question_direct(
            question=content,
            process=process,
            previous_state=previous_state,
            recent_messages=get_recent_messages(owner_id, session_id, limit=8),
            reference_doc_count=active_reference_doc_count,
        )

        answer = next_state.get("llm_response", "답변을 생성하지 못했습니다.")
        assistant_message = append_message(owner_id, session_id, "assistant", answer)
        assistant_message_id = str(assistant_message.get("id") or "")

        try:
            upsert_session_memory(owner_id, session_id, process, next_state)
        except Exception as error:
            print(f"[WARN] failed to save postgres chat memory: {error}")

        if session.get("title") == "새 채팅":
            update_session_title(owner_id, session_id, _session_title_from_text(content))

        updated = get_session(owner_id, session_id)
        _log_chat_metric(
            owner_id=owner_id,
            session_id=session_id,
            question=content,
            answer=answer,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            reference_doc_count=active_reference_doc_count,
            state=next_state,
            status="success",
            total_duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return updated, answer
    except Exception as error:
        _log_chat_metric(
            owner_id=owner_id,
            session_id=session_id,
            question=content,
            answer="",
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            reference_doc_count=active_reference_doc_count,
            state=next_state,
            status="error",
            error_message=str(error),
            total_duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        raise


def set_reference_doc_count(owner_id: str, session_id: str, reference_doc_count: int) -> dict:
    update_session_reference_doc_count(owner_id, session_id, reference_doc_count)
    return get_session(owner_id, session_id)


def stream_user_message_events(owner_id: str, session_id: str, content: str, reference_doc_count: int | None = None) -> Generator[str, None, None]:
    if not content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="질문이 비어 있습니다.")

    started = perf_counter()
    first_token_ms: float | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    next_state: dict | None = None

    session = get_session(owner_id, session_id)
    process = session.get("process", "MP")
    if reference_doc_count is not None:
        update_session_reference_doc_count(owner_id, session_id, reference_doc_count)
        session = get_session(owner_id, session_id)
    active_reference_doc_count = int(session.get("reference_doc_count") or 6)

    user_message = append_message(owner_id, session_id, "user", content)
    user_message_id = str(user_message.get("id") or "")
    yield _to_ndjson({"type": "user_ack"})

    previous_state = session.get("agentic_state") or {}
    try:
        pg_state = get_session_memory(owner_id, session_id, process)
        if pg_state:
            previous_state = pg_state
    except Exception as error:
        print(f"[WARN] failed to load postgres chat memory: {error}")

    stream = answer_question_stream(
        question=content,
        process=process,
        previous_state=previous_state,
        recent_messages=get_recent_messages(owner_id, session_id, limit=8),
        reference_doc_count=active_reference_doc_count,
    )

    full_answer_parts: list[str] = []

    try:
        while True:
            try:
                chunk = next(stream)
            except StopIteration as stop:
                next_state = stop.value
                break

            if not chunk:
                continue

            if first_token_ms is None:
                first_token_ms = round((perf_counter() - started) * 1000, 2)
            full_answer_parts.append(chunk)
            yield _to_ndjson({"type": "delta", "content": chunk})
    except Exception as error:
        _log_chat_metric(
            owner_id=owner_id,
            session_id=session_id,
            question=content,
            answer="".join(full_answer_parts),
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            reference_doc_count=active_reference_doc_count,
            state=next_state,
            status="error",
            error_message=str(error),
            first_token_ms=first_token_ms,
            total_duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        yield _to_ndjson({"type": "error", "message": f"답변 스트리밍 중 오류가 발생했습니다: {error}"})
        return

    full_answer = "".join(full_answer_parts).strip()
    if next_state is None:
        next_state = {"llm_response": full_answer, "retrieval_debug": {"reference_doc_count": active_reference_doc_count}}

    if not full_answer:
        full_answer = next_state.get("llm_response", "답변을 생성하지 못했습니다.")

    assistant_message = append_message(owner_id, session_id, "assistant", full_answer)
    assistant_message_id = str(assistant_message.get("id") or "")

    try:
        upsert_session_memory(owner_id, session_id, process, next_state)
    except Exception as error:
        print(f"[WARN] failed to save postgres chat memory: {error}")

    if session.get("title") == "새 채팅":
        update_session_title(owner_id, session_id, _session_title_from_text(content))

    updated = get_session(owner_id, session_id)
    _log_chat_metric(
        owner_id=owner_id,
        session_id=session_id,
        question=content,
        answer=full_answer,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        reference_doc_count=active_reference_doc_count,
        state=next_state,
        status="success",
        first_token_ms=first_token_ms,
        total_duration_ms=round((perf_counter() - started) * 1000, 2),
    )
    yield _to_ndjson({
        "type": "final",
        "session": updated,
        "answer": full_answer,
    })
