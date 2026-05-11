from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.core.auth import SessionUser, get_current_user
from app.core.visitor import get_or_create_visitor_id, set_visitor_cookie
from app.schemas.chat import (
    CreateChatSessionRequest,
    DeleteChatSessionResponse,
    SendMessageRequest,
    SendMessageResponse,
    UpdateReferenceDocCountRequest,
)
from app.services.chat_service import (
    create_session,
    delete_session,
    get_session,
    list_sessions,
    send_user_message,
    set_reference_doc_count,
    stream_user_message_events,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _build_owner_id(username: str, visitor_id: str) -> str:
    return f"{username}:{visitor_id}"


@router.get("/sessions")
def get_sessions(
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    return list_sessions(owner_id)


@router.post("/sessions")
def post_session(
    payload: CreateChatSessionRequest,
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    return create_session(owner_id, title=payload.title, process=payload.process, reference_doc_count=payload.reference_doc_count)


@router.get("/sessions/{session_id}")
def get_single_session(
    session_id: str,
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    return get_session(owner_id, session_id)


@router.delete("/sessions/{session_id}", response_model=DeleteChatSessionResponse)
def remove_session(
    session_id: str,
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    delete_session(owner_id, session_id)
    return DeleteChatSessionResponse(session_id=session_id)




@router.patch("/sessions/{session_id}/reference-doc-count")
def patch_reference_doc_count(
    session_id: str,
    payload: UpdateReferenceDocCountRequest,
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    return set_reference_doc_count(owner_id, session_id, payload.reference_doc_count)


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def post_message(
    session_id: str,
    payload: SendMessageRequest,
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    set_visitor_cookie(response, visitor_id)
    owner_id = _build_owner_id(current_user.username, visitor_id)
    session, answer = send_user_message(owner_id, session_id, payload.content, payload.reference_doc_count)
    return SendMessageResponse(session=session, answer=answer)


@router.post("/sessions/{session_id}/messages/stream")
def post_message_stream(
    session_id: str,
    payload: SendMessageRequest,
    request: Request,
    response: Response,
    current_user: SessionUser = Depends(get_current_user),
):
    visitor_id, _ = get_or_create_visitor_id(request)
    owner_id = _build_owner_id(current_user.username, visitor_id)

    streaming_response = StreamingResponse(
        stream_user_message_events(owner_id, session_id, payload.content, payload.reference_doc_count),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
    set_visitor_cookie(streaming_response, visitor_id)
    return streaming_response
