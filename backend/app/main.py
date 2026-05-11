from __future__ import annotations

from threading import Lock
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers.auth import router as auth_router
from app.routers.chat import router as chat_router
from app.routers.inform import router as inform_router
from app.routers.monitoring import router as monitoring_router
from app.services.chat_pg_store import init_chat_pg_tables, migrate_json_store_if_needed
from app.services.monitoring_store import init_monitoring_tables, insert_event

app = FastAPI(title=settings.app_name)

_ACTIVE_REQUESTS = 0
_ACTIVE_LOCK = Lock()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def monitoring_request_middleware(request: Request, call_next):
    """API 응답 시간과 동시 처리 요청 수를 PostgreSQL에 기록합니다."""

    global _ACTIVE_REQUESTS

    path = request.url.path
    skip_log = path in {"/api/health"} or path.startswith("/docs") or path.startswith("/openapi")

    with _ACTIVE_LOCK:
        _ACTIVE_REQUESTS += 1
        active_now = _ACTIVE_REQUESTS

    started = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        duration_ms = round((perf_counter() - started) * 1000, 2)
        with _ACTIVE_LOCK:
            _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)

        if not skip_log and path.startswith("/api") and path != "/api/monitoring/heartbeat":
            try:
                insert_event(
                    event_type="api_request",
                    method=request.method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    active_requests=active_now,
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent", ""),
                    extra={"query": str(request.url.query or "")},
                )
            except Exception as error:
                print(f"[WARN] failed to save monitoring request event: {error}")


app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(inform_router)
app.include_router(monitoring_router)


@app.get("/api/health")
def health_check():
    return {"ok": True, "env": settings.app_env}


@app.on_event("startup")
def startup_init_storage():
    try:
        init_chat_pg_tables()
        init_monitoring_tables()
        migration = migrate_json_store_if_needed()
        print("[INFO] chat_sessions / chat_messages / chat_session_memory tables are ready.")
        print("[INFO] monitoring tables are ready.")
        if migration.get("migrated"):
            print(
                f"[INFO] json chat store migrated to postgres: "
                f"sessions={migration.get('sessions', 0)}, messages={migration.get('messages', 0)}"
            )
    except Exception as error:
        print(f"[WARN] failed to initialize postgres storage: {error}")
