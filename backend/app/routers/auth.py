from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import SessionUser, get_current_token, get_current_user, session_manager
from app.core.config import settings
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    if payload.username != settings.admin_username or payload.password != settings.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    token = session_manager.create(username=settings.admin_username, email=settings.admin_email)
    return LoginResponse(access_token=token, username=settings.admin_username, email=settings.admin_email)


@router.get("/me")
def me(current_user: SessionUser = Depends(get_current_user)):
    return {"username": current_user.username, "email": current_user.email}


@router.post("/logout")
def logout(token: str = Depends(get_current_token)):
    session_manager.delete(token)
    return {"ok": True}
