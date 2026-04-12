from fastapi import APIRouter, Depends

from app.core.auth import SessionUser, get_current_user
from app.schemas.chat import CreateChatSessionRequest, SendMessageRequest, SendMessageResponse
from app.services.chat_service import create_session, get_session, list_sessions, send_user_message

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/sessions")
def get_sessions(current_user: SessionUser = Depends(get_current_user)):
    return list_sessions(current_user.username)


@router.post("/sessions")
def post_session(payload: CreateChatSessionRequest, current_user: SessionUser = Depends(get_current_user)):
    return create_session(current_user.username, title=payload.title, process=payload.process)


@router.get("/sessions/{session_id}")
def get_single_session(session_id: str, current_user: SessionUser = Depends(get_current_user)):
    return get_session(current_user.username, session_id)


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def post_message(session_id: str, payload: SendMessageRequest, current_user: SessionUser = Depends(get_current_user)):
    session, answer = send_user_message(current_user.username, session_id, payload.content)
    return SendMessageResponse(session=session, answer=answer)
