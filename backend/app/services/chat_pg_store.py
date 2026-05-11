from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg2
from dotenv import load_dotenv
from fastapi import HTTPException, status
from psycopg2.extras import Json, RealDictCursor

from app.core.json_store import CHAT_STORE_PATH, load_json

load_dotenv()

PG_CONN_STR = os.getenv("PG_CONN_STR") or "host=localhost dbname=vectorDB user=postgres password=1541"
DEFAULT_REFERENCE_DOC_COUNT = int(os.getenv("RAG_FINAL_CONTEXT_DOCS", "6"))
ALLOWED_REFERENCE_DOC_COUNTS = {1, 3, 5, 10, 20, 30}

CREATE_CHAT_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    process TEXT NOT NULL DEFAULT 'MP',
    reference_doc_count INTEGER NOT NULL DEFAULT 6,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

CREATE_CHAT_MESSAGES_SQL = """
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

CREATE_CHAT_MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS chat_session_memory (
    session_id TEXT PRIMARY KEY REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    process TEXT,
    current_line TEXT,
    current_equip TEXT,
    current_error TEXT,
    last_reference_doc_id TEXT,
    last_reference_summary TEXT,
    last_intent TEXT,
    last_symptom_keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    recent_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
    full_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

ALTER_TABLE_SQL = [
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS reference_doc_count INTEGER NOT NULL DEFAULT 6;",
]

CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner_id_updated_at ON chat_sessions(owner_id, updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id_created_at ON chat_messages(session_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_chat_session_memory_owner_id ON chat_session_memory(owner_id);",
    "CREATE INDEX IF NOT EXISTS idx_chat_session_memory_updated_at ON chat_session_memory(updated_at DESC);",
]


def _get_conn():
    return psycopg2.connect(PG_CONN_STR)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(UTC)




def _normalize_reference_doc_count(value: Any) -> int:
    try:
        count = int(value)
    except Exception:
        count = DEFAULT_REFERENCE_DOC_COUNT
    if count not in ALLOWED_REFERENCE_DOC_COUNTS:
        count = DEFAULT_REFERENCE_DOC_COUNT
    return count

def _default_json_store() -> dict[str, Any]:
    return {"users": {}}


def _memory_fields_from_state(state: dict[str, Any] | None, process: str) -> dict[str, Any]:
    state = dict(state or {})
    memory = dict(state.get("conversation_memory", {}) or {})
    selected_doc = dict(state.get("selected_doc", {}) or {})

    current_line = memory.get("current_line") or selected_doc.get("라인") or ""
    current_equip = memory.get("current_equip") or selected_doc.get("설비명") or ""
    current_error = memory.get("current_error") or selected_doc.get("에러명") or ""
    last_reference_doc_id = memory.get("last_reference_doc_id") or selected_doc.get("id") or ""
    last_reference_summary = memory.get("last_reference_summary") or selected_doc.get("점검이력") or ""
    last_intent = memory.get("last_intent") or state.get("retrieval_debug", {}).get("intent") or ""
    last_symptom_keywords = list(memory.get("last_symptom_keywords", []) or [])
    recent_questions = list(memory.get("recent_questions", []) or [])
    recent_answers = list(memory.get("recent_answers", []) or [])

    return {
        "process": memory.get("process") or process,
        "current_line": current_line,
        "current_equip": current_equip,
        "current_error": current_error,
        "last_reference_doc_id": str(last_reference_doc_id) if last_reference_doc_id else "",
        "last_reference_summary": str(last_reference_summary or ""),
        "last_intent": str(last_intent or ""),
        "last_symptom_keywords": last_symptom_keywords,
        "recent_questions": recent_questions,
        "recent_answers": recent_answers,
        "full_state": state,
    }


def init_chat_pg_tables() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_CHAT_SESSIONS_SQL)
            cur.execute(CREATE_CHAT_MESSAGES_SQL)
            cur.execute(CREATE_CHAT_MEMORY_SQL)
            for sql in ALTER_TABLE_SQL:
                cur.execute(sql)
            for sql in CREATE_INDEX_SQL:
                cur.execute(sql)
        conn.commit()


def _count_sessions() -> int:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_sessions")
            row = cur.fetchone()
    return int(row[0] or 0)


def migrate_json_store_if_needed() -> dict[str, int]:
    if not Path(CHAT_STORE_PATH).exists():
        return {"sessions": 0, "messages": 0, "migrated": 0}
    if _count_sessions() > 0:
        return {"sessions": 0, "messages": 0, "migrated": 0}

    store = load_json(CHAT_STORE_PATH, _default_json_store())
    users = dict(store.get("users", {}) or {})
    migrated_sessions = 0
    migrated_messages = 0

    with _get_conn() as conn:
        with conn.cursor() as cur:
            for owner_id, sessions in users.items():
                for session in sessions or []:
                    session_id = str(session.get("id") or uuid4().hex)
                    title = str(session.get("title") or "새 채팅")
                    process = str(session.get("process") or "MP")
                    reference_doc_count = _normalize_reference_doc_count(session.get("reference_doc_count") or session.get("agentic_state", {}).get("retrieval_debug", {}).get("reference_doc_count") or DEFAULT_REFERENCE_DOC_COUNT)
                    created_at = _parse_dt(session.get("created_at"))
                    updated_at = _parse_dt(session.get("updated_at"))

                    cur.execute(
                        """
                        INSERT INTO chat_sessions (session_id, owner_id, title, process, reference_doc_count, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id) DO NOTHING
                        """,
                        (session_id, owner_id, title, process, reference_doc_count, created_at, updated_at),
                    )
                    migrated_sessions += 1

                    for message in session.get("messages", []) or []:
                        message_id = str(message.get("id") or uuid4().hex)
                        role = str(message.get("role") or "assistant")
                        content = str(message.get("content") or "")
                        msg_created_at = _parse_dt(message.get("created_at"))
                        cur.execute(
                            """
                            INSERT INTO chat_messages (message_id, session_id, owner_id, role, content, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (message_id) DO NOTHING
                            """,
                            (message_id, session_id, owner_id, role, content, msg_created_at),
                        )
                        migrated_messages += 1

                    state = dict(session.get("agentic_state") or {})
                    fields = _memory_fields_from_state(state, process)
                    cur.execute(
                        """
                        INSERT INTO chat_session_memory (
                            session_id, owner_id, process, current_line, current_equip, current_error,
                            last_reference_doc_id, last_reference_summary, last_intent,
                            last_symptom_keywords, recent_questions, recent_answers, full_state,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                            %s, %s
                        )
                        ON CONFLICT (session_id) DO NOTHING
                        """,
                        (
                            session_id,
                            owner_id,
                            fields["process"],
                            fields["current_line"],
                            fields["current_equip"],
                            fields["current_error"],
                            fields["last_reference_doc_id"],
                            fields["last_reference_summary"],
                            fields["last_intent"],
                            Json(fields["last_symptom_keywords"]),
                            Json(fields["recent_questions"]),
                            Json(fields["recent_answers"]),
                            Json(fields["full_state"]),
                            created_at,
                            updated_at,
                        ),
                    )
        conn.commit()

    return {"sessions": migrated_sessions, "messages": migrated_messages, "migrated": 1}


def list_sessions(owner_id: str) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT session_id AS id, title, process, reference_doc_count, created_at, updated_at
                FROM chat_sessions
                WHERE owner_id = %s
                ORDER BY updated_at DESC
                """,
                (owner_id,),
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def create_session(owner_id: str, title: str | None = None, process: str = "MP", reference_doc_count: int | None = None) -> dict[str, Any]:
    session_id = uuid4().hex
    session_title = title or "새 채팅"
    process = process.upper() or "MP"
    reference_doc_count = _normalize_reference_doc_count(reference_doc_count or DEFAULT_REFERENCE_DOC_COUNT)

    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO chat_sessions (session_id, owner_id, title, process, reference_doc_count, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING session_id AS id, title, process, reference_doc_count, created_at, updated_at
                """,
                (session_id, owner_id, session_title, process, reference_doc_count),
            )
            session = dict(cur.fetchone())
        conn.commit()

    ensure_session_memory(owner_id, session_id, process)
    session["messages"] = []
    session["agentic_state"] = {}
    return session


def _load_messages(owner_id: str, session_id: str) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT message_id AS id, role, content, created_at
                FROM chat_messages
                WHERE owner_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (owner_id, session_id),
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_recent_messages(owner_id: str, session_id: str, limit: int = 8) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM (
                    SELECT message_id AS id, role, content, created_at
                    FROM chat_messages
                    WHERE owner_id = %s AND session_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) t
                ORDER BY created_at ASC
                """,
                (owner_id, session_id, limit),
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_session_memory(owner_id: str, session_id: str, process: str) -> dict[str, Any] | None:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT process, current_line, current_equip, current_error,
                       last_reference_doc_id, last_reference_summary, last_intent,
                       last_symptom_keywords, recent_questions, recent_answers, full_state
                FROM chat_session_memory
                WHERE session_id = %s AND owner_id = %s
                """,
                (session_id, owner_id),
            )
            row = cur.fetchone()

    if not row:
        return None

    full_state = dict(row.get("full_state") or {})
    memory = dict(full_state.get("conversation_memory", {}) or {})
    memory["process"] = row.get("process") or memory.get("process") or process
    memory["current_line"] = row.get("current_line") or memory.get("current_line") or ""
    memory["current_equip"] = row.get("current_equip") or memory.get("current_equip") or ""
    memory["current_error"] = row.get("current_error") or memory.get("current_error") or ""
    memory["last_reference_doc_id"] = row.get("last_reference_doc_id") or memory.get("last_reference_doc_id") or ""
    memory["last_reference_summary"] = row.get("last_reference_summary") or memory.get("last_reference_summary") or ""
    memory["last_intent"] = row.get("last_intent") or memory.get("last_intent") or ""
    memory["last_symptom_keywords"] = list(row.get("last_symptom_keywords") or memory.get("last_symptom_keywords") or [])
    memory["recent_questions"] = list(row.get("recent_questions") or memory.get("recent_questions") or [])
    memory["recent_answers"] = list(row.get("recent_answers") or memory.get("recent_answers") or [])

    full_state["conversation_memory"] = memory
    return full_state


def get_session(owner_id: str, session_id: str) -> dict[str, Any]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT session_id AS id, title, process, reference_doc_count, created_at, updated_at
                FROM chat_sessions
                WHERE owner_id = %s AND session_id = %s
                """,
                (owner_id, session_id),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")

    session = dict(row)
    session["messages"] = _load_messages(owner_id, session_id)
    session["agentic_state"] = get_session_memory(owner_id, session_id, session.get("process", "MP")) or {}
    return session


def append_message(owner_id: str, session_id: str, role: str, content: str, *, message_id: str | None = None) -> dict[str, Any]:
    message_id = message_id or uuid4().hex
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO chat_messages (message_id, session_id, owner_id, role, content, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                RETURNING message_id AS id, role, content, created_at
                """,
                (message_id, session_id, owner_id, role, content),
            )
            message = dict(cur.fetchone())
            cur.execute(
                "UPDATE chat_sessions SET updated_at = NOW() WHERE owner_id = %s AND session_id = %s",
                (owner_id, session_id),
            )
        conn.commit()
    return message


def update_session_title(owner_id: str, session_id: str, title: str) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_sessions SET title = %s, updated_at = NOW() WHERE owner_id = %s AND session_id = %s",
                (title, owner_id, session_id),
            )
        conn.commit()


def update_session_reference_doc_count(owner_id: str, session_id: str, reference_doc_count: int) -> int:
    normalized = _normalize_reference_doc_count(reference_doc_count)
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_sessions SET reference_doc_count = %s, updated_at = NOW() WHERE owner_id = %s AND session_id = %s",
                (normalized, owner_id, session_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")
        conn.commit()
    return normalized


def upsert_session_memory(owner_id: str, session_id: str, process: str, state: dict[str, Any] | None) -> None:
    fields = _memory_fields_from_state(state, process)
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_session_memory (
                    session_id,
                    owner_id,
                    process,
                    current_line,
                    current_equip,
                    current_error,
                    last_reference_doc_id,
                    last_reference_summary,
                    last_intent,
                    last_symptom_keywords,
                    recent_questions,
                    recent_answers,
                    full_state,
                    created_at,
                    updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    NOW(), NOW()
                )
                ON CONFLICT (session_id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    process = EXCLUDED.process,
                    current_line = EXCLUDED.current_line,
                    current_equip = EXCLUDED.current_equip,
                    current_error = EXCLUDED.current_error,
                    last_reference_doc_id = EXCLUDED.last_reference_doc_id,
                    last_reference_summary = EXCLUDED.last_reference_summary,
                    last_intent = EXCLUDED.last_intent,
                    last_symptom_keywords = EXCLUDED.last_symptom_keywords,
                    recent_questions = EXCLUDED.recent_questions,
                    recent_answers = EXCLUDED.recent_answers,
                    full_state = EXCLUDED.full_state,
                    updated_at = NOW()
                """,
                (
                    session_id,
                    owner_id,
                    fields["process"],
                    fields["current_line"],
                    fields["current_equip"],
                    fields["current_error"],
                    fields["last_reference_doc_id"],
                    fields["last_reference_summary"],
                    fields["last_intent"],
                    Json(fields["last_symptom_keywords"]),
                    Json(fields["recent_questions"]),
                    Json(fields["recent_answers"]),
                    Json(fields["full_state"]),
                ),
            )
        conn.commit()


def ensure_session_memory(owner_id: str, session_id: str, process: str) -> None:
    if get_session_memory(owner_id, session_id, process) is None:
        upsert_session_memory(
            owner_id=owner_id,
            session_id=session_id,
            process=process,
            state={
                "conversation_memory": {
                    "process": process,
                    "recent_questions": [],
                    "recent_answers": [],
                    "last_symptom_keywords": [],
                }
            },
        )


def delete_session(owner_id: str, session_id: str) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_sessions WHERE owner_id = %s AND session_id = %s",
                (owner_id, session_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채팅 세션을 찾을 수 없습니다.")
        conn.commit()
