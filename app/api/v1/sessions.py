# -*- coding: utf-8 -*-
"""챗봇 대화 라우터 — 로그인한 회사가 자기 대화 세션/메시지를 보고 지운다.

/me 하위: 대상이 '내 회사'다(company_id는 토큰에서). 대화 생성·추가는 여전히
POST /agent/chat 담당이고, 여기는 조회와 삭제만. 없거나 남의 세션은 404(존재 은닉).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, get_authenticated_user, get_chat_query_service
from app.api.v1.schemas.chat import SessionDetailResponse, SessionListResponse
from app.infra.db.repositories.chat_repository import SessionForbiddenError
from app.services.chat_query_service import ChatQueryService

router = APIRouter(prefix="/me/sessions", tags=["chat-sessions"])


@router.get("", response_model=SessionListResponse)
def list_my_sessions(
    page: int = Query(default=1, ge=1, description="1-based. 범위 밖이면 빈 배열."),
    service: ChatQueryService = Depends(get_chat_query_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> SessionListResponse:
    """내 대화 세션 목록 — 마지막 활동 최근순."""
    return service.list_sessions(company_id=int(current_user.company_id), page=page)


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_my_session(
    session_id: str,
    service: ChatQueryService = Depends(get_chat_query_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> SessionDetailResponse:
    """세션 상세 — 메시지 시간순. 없거나 남의 세션이면 404."""
    try:
        return service.get_session(
            session_id=session_id, company_id=int(current_user.company_id))
    except (SessionForbiddenError, ValueError):
        # SessionForbiddenError=없음/남의 것, ValueError=잘못된 uuid 형식 → 존재 은닉 404
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾을 수 없습니다.")


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_session(
    session_id: str,
    service: ChatQueryService = Depends(get_chat_query_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> None:
    """대화방 삭제 — 목록에서 사라진다(소프트). 이미 지운 세션도 404."""
    try:
        service.delete_session(
            session_id=session_id, company_id=int(current_user.company_id))
    except (SessionForbiddenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾을 수 없습니다.")


@router.delete("/{session_id}/last-turn", status_code=status.HTTP_204_NO_CONTENT)
def delete_last_turn(
    session_id: str,
    service: ChatQueryService = Depends(get_chat_query_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> None:
    """마지막 턴 취소 — 방금 주고받은 질문·답변을 지우고 문맥을 비운다."""
    try:
        deleted = service.delete_last_turn(
            session_id=session_id, company_id=int(current_user.company_id))
    except (SessionForbiddenError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾을 수 없습니다.")
    if not deleted:
        # 빈 대화방 — 취소할 턴이 없다. 세션 자체는 있으므로 존재 은닉과 무관.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="취소할 대화가 없습니다.")
