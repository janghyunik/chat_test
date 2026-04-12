from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from starlette.responses import Response

VISITOR_COOKIE_NAME = "visitor_id"
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 90  # 90일


def get_or_create_visitor_id(request: Request) -> tuple[str, bool]:
    """
    브라우저마다 고유한 visitor_id를 발급합니다.
    return: (visitor_id, 새로_만들었는지)
    """
    visitor_id = request.cookies.get(VISITOR_COOKIE_NAME)
    if visitor_id:
        return visitor_id, False
    return str(uuid4()), True


def set_visitor_cookie(response: Response, visitor_id: str) -> None:
    response.set_cookie(
        key=VISITOR_COOKIE_NAME,
        value=visitor_id,
        httponly=True,
        samesite="lax",
        secure=False,  # HTTPS 운영 전환 시 True 권장
        max_age=VISITOR_COOKIE_MAX_AGE,
        path="/",
    )
