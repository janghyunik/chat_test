from __future__ import annotations

import os
from typing import Any

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

load_dotenv()

PG_CONN_STR = os.getenv("PG_CONN_STR") or "host=localhost dbname=vectorDB user=postgres password=1541"

CREATE_MEMORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chat_session_memory (
    session_id TEXT PRIMARY KEY,
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

CREATE_MEMORY_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_chat_session_memory_owner_id ON chat_session_memory(owner_id);",
    "CREATE INDEX IF NOT EXISTS idx_chat_session_memory_updated_at ON chat_session_memory(updated_at DESC);",
]


def _get_conn():
    return psycopg2.connect(PG_CONN_STR)


def init_chat_memory_tables() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_MEMORY_TABLE_SQL)
            for sql in CREATE_MEMORY_INDEX_SQL:
                cur.execute(sql)
        conn.commit()


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


def get_session_memory(owner_id: str, session_id: str, process: str) -> dict[str, Any] | None:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
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


def delete_session_memory(owner_id: str, session_id: str) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM chat_session_memory WHERE session_id = %s AND owner_id = %s",
                (session_id, owner_id),
            )
        conn.commit()
