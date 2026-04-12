from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import Dict
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, status

from app.core.config import settings

@dataclass
class SessionUser:
    username: str
    email: str
    expires_at: datetime


class SessionManager:
    """
    아주 단순한 메모리 기반 세션 관리자입니다.
    - 초기 전환 단계에서는 이해하기 쉽게 메모리로 유지합니다.
    - 서버 재시작 시에는 세션이 초기화되므로 다시 로그인해야 합니다.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionUser] = {}

    def create(self, username: str, email: str) -> str:
        token = uuid4().hex
        expires_at = datetime.now(UTC) + timedelta(hours=settings.session_expire_hours)
        self._sessions[token] = SessionUser(username=username, email=email, expires_at=expires_at)
        return token

    def get(self, token: str | None) -> SessionUser:
        if not token or token not in self._sessions:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")

        session = self._sessions[token]
        if session.expires_at < datetime.now(UTC):
            self._sessions.pop(token, None)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="세션이 만료되었습니다.")
        return session

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


session_manager = SessionManager()


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization 헤더가 없습니다.")

    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer 토큰 형식이 아닙니다.")
    return authorization[len(prefix):].strip()


def get_current_user(authorization: str | None = Header(default=None)) -> SessionUser:
    token = _extract_bearer_token(authorization)
    return session_manager.get(token)


def get_current_token(authorization: str | None = Header(default=None)) -> str:
    return _extract_bearer_token(authorization)
