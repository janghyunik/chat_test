from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.core.auth import SessionUser, get_current_user
from app.core.visitor import get_or_create_visitor_id, set_visitor_cookie
from app.services.monitoring_store import insert_event, upsert_visitor_session

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _build_owner_id(username: str, visitor_id: str) -> str:
    return f"{username}:{visitor_id}"


@router.post("/heartbeat")
def heartbeat(
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    path = request.headers.get("x-page-path", "") or request.url.path
    ip_address = _client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    upsert_visitor_session(
        visitor_id=visitor_id,
        owner_id=owner_id,
        username=current_user.username,
        ip_address=ip_address,
        user_agent=user_agent,
        path=path,
    )
    insert_event(
        event_type="heartbeat",
        visitor_id=visitor_id,
        owner_id=owner_id,
        username=current_user.username,
        method="POST",
        path=path,
        status_code=200,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={"page_path": path},
    )
    return {"ok": True}
