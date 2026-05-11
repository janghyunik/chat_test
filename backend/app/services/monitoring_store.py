from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, RealDictCursor

load_dotenv()

PG_CONN_STR = os.getenv("PG_CONN_STR") or "host=localhost dbname=vectorDB user=postgres password=1541"

CREATE_VISITOR_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS monitoring_visitor_sessions (
    visitor_id TEXT PRIMARY KEY,
    owner_id TEXT,
    username TEXT,
    ip_address TEXT,
    user_agent TEXT,
    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_path TEXT,
    heartbeat_count INTEGER NOT NULL DEFAULT 0
);
"""

CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS monitoring_events (
    event_id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    visitor_id TEXT,
    owner_id TEXT,
    username TEXT,
    method TEXT,
    path TEXT,
    status_code INTEGER,
    duration_ms DOUBLE PRECISION,
    active_requests INTEGER,
    ip_address TEXT,
    user_agent TEXT,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

CREATE_CHAT_INTERACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS monitoring_chat_interactions (
    interaction_id BIGSERIAL PRIMARY KEY,
    owner_id TEXT NOT NULL,
    visitor_id TEXT,
    username TEXT,
    session_id TEXT NOT NULL,
    user_message_id TEXT,
    assistant_message_id TEXT,
    question TEXT NOT NULL,
    question_normalized TEXT NOT NULL,
    answer_chars INTEGER NOT NULL DEFAULT 0,
    reference_doc_count INTEGER,
    intent TEXT,
    is_follow_up BOOLEAN,
    document_count INTEGER,
    candidate_count_first INTEGER,
    candidate_count_second INTEGER,
    candidate_count_final INTEGER,
    retrieval_debug JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    first_token_ms DOUBLE PRECISION,
    total_duration_ms DOUBLE PRECISION,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

ALTER_SQL = [
    "ALTER TABLE monitoring_chat_interactions ADD COLUMN IF NOT EXISTS user_message_id TEXT;",
    "ALTER TABLE monitoring_chat_interactions ADD COLUMN IF NOT EXISTS assistant_message_id TEXT;",
    "ALTER TABLE monitoring_chat_interactions ADD COLUMN IF NOT EXISTS first_token_ms DOUBLE PRECISION;",
    "ALTER TABLE monitoring_chat_interactions ADD COLUMN IF NOT EXISTS candidate_count_first INTEGER;",
    "ALTER TABLE monitoring_chat_interactions ADD COLUMN IF NOT EXISTS candidate_count_second INTEGER;",
    "ALTER TABLE monitoring_chat_interactions ADD COLUMN IF NOT EXISTS candidate_count_final INTEGER;",
]

INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_monitoring_events_created_at ON monitoring_events(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_events_type_created_at ON monitoring_events(event_type, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_events_visitor_created_at ON monitoring_events(visitor_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_visitor_last_seen ON monitoring_visitor_sessions(last_seen_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_chat_created_at ON monitoring_chat_interactions(created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_chat_session_created_at ON monitoring_chat_interactions(session_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_monitoring_chat_question_norm ON monitoring_chat_interactions(question_normalized);",
]


def _get_conn():
    return psycopg2.connect(PG_CONN_STR)


def init_monitoring_tables() -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_VISITOR_SESSIONS_SQL)
            cur.execute(CREATE_EVENTS_SQL)
            cur.execute(CREATE_CHAT_INTERACTIONS_SQL)
            for sql in ALTER_SQL:
                cur.execute(sql)
            for sql in INDEX_SQL:
                cur.execute(sql)
        conn.commit()


def normalize_question(question: str) -> str:
    return " ".join(str(question or "").strip().lower().split())[:500]


def upsert_visitor_session(
    *,
    visitor_id: str,
    owner_id: str | None = None,
    username: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    path: str | None = None,
) -> None:
    if not visitor_id:
        return
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitoring_visitor_sessions (
                    visitor_id, owner_id, username, ip_address, user_agent,
                    first_seen_at, last_seen_at, last_path, heartbeat_count
                ) VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), %s, 1)
                ON CONFLICT (visitor_id) DO UPDATE SET
                    owner_id = COALESCE(EXCLUDED.owner_id, monitoring_visitor_sessions.owner_id),
                    username = COALESCE(EXCLUDED.username, monitoring_visitor_sessions.username),
                    ip_address = COALESCE(EXCLUDED.ip_address, monitoring_visitor_sessions.ip_address),
                    user_agent = COALESCE(EXCLUDED.user_agent, monitoring_visitor_sessions.user_agent),
                    last_seen_at = NOW(),
                    last_path = COALESCE(EXCLUDED.last_path, monitoring_visitor_sessions.last_path),
                    heartbeat_count = monitoring_visitor_sessions.heartbeat_count + 1
                """,
                (visitor_id, owner_id, username, ip_address, user_agent, path),
            )
        conn.commit()


def insert_event(
    *,
    event_type: str,
    visitor_id: str | None = None,
    owner_id: str | None = None,
    username: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    duration_ms: float | None = None,
    active_requests: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitoring_events (
                    event_type, visitor_id, owner_id, username, method, path,
                    status_code, duration_ms, active_requests, ip_address, user_agent, extra, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                """,
                (
                    event_type,
                    visitor_id,
                    owner_id,
                    username,
                    method,
                    path,
                    status_code,
                    duration_ms,
                    active_requests,
                    ip_address,
                    user_agent,
                    Json(extra or {}),
                ),
            )
        conn.commit()


def insert_chat_interaction(
    *,
    owner_id: str,
    session_id: str,
    question: str,
    visitor_id: str | None = None,
    username: str | None = None,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    answer: str | None = None,
    reference_doc_count: int | None = None,
    retrieval_debug: dict[str, Any] | None = None,
    status: str = "success",
    error_message: str | None = None,
    first_token_ms: float | None = None,
    total_duration_ms: float | None = None,
) -> None:
    retrieval_debug = retrieval_debug or {}
    answer_text = answer or ""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO monitoring_chat_interactions (
                    owner_id, visitor_id, username, session_id, user_message_id, assistant_message_id,
                    question, question_normalized, answer_chars, reference_doc_count,
                    intent, is_follow_up, document_count,
                    candidate_count_first, candidate_count_second, candidate_count_final,
                    retrieval_debug, status, error_message, first_token_ms, total_duration_ms, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s, NOW()
                )
                """,
                (
                    owner_id,
                    visitor_id,
                    username,
                    session_id,
                    user_message_id,
                    assistant_message_id,
                    question,
                    normalize_question(question),
                    len(answer_text),
                    reference_doc_count,
                    retrieval_debug.get("intent"),
                    retrieval_debug.get("is_follow_up"),
                    retrieval_debug.get("reference_doc_count") or retrieval_debug.get("document_count"),
                    retrieval_debug.get("candidate_count_first"),
                    retrieval_debug.get("candidate_count_second"),
                    retrieval_debug.get("candidate_count_final"),
                    Json(retrieval_debug),
                    status,
                    error_message,
                    first_token_ms,
                    total_duration_ms,
                ),
            )
        conn.commit()


def get_recent_monitoring_snapshot(minutes: int = 5) -> dict[str, Any]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS active_visitors
                FROM monitoring_visitor_sessions
                WHERE last_seen_at >= NOW() - (%s || ' minutes')::interval
                """,
                (minutes,),
            )
            active = dict(cur.fetchone() or {}).get("active_visitors", 0)

            cur.execute(
                """
                SELECT COUNT(*) AS requests,
                       COALESCE(AVG(duration_ms), 0) AS avg_ms,
                       COALESCE(MAX(active_requests), 0) AS max_active_requests
                FROM monitoring_events
                WHERE created_at >= NOW() - (%s || ' minutes')::interval
                  AND event_type = 'api_request'
                """,
                (minutes,),
            )
            api = dict(cur.fetchone() or {})
    return {"active_visitors": int(active or 0), **api}
