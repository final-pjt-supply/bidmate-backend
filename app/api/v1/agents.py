# -*- coding: utf-8 -*-
"""POST /agent/chat — 대화 에이전트 진입점.

HTTP 경계만 담당한다 — 세션 왕복은 AgentChatService, 에이전트 로직은 별도
서비스(루프백 8010의 POST /turn) 소관. sync def라 FastAPI가 스레드풀에서
돌린다(에이전트 호출은 검색+Bedrock 합성이라 수 초~수십 초 블로킹된다).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.chat_service import AgentChatService, SessionBusyError
from app.api.deps import (
    CurrentUser,
    enforce_chat_limits,
    get_agent_chat_service,
    get_authenticated_user,
)
from app.api.v1.schemas.agent import AgentChatRequest, AgentChatResponse
from app.infra.db.repositories.chat_repository import SessionForbiddenError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse)
def chat(
    payload: AgentChatRequest,
    service: AgentChatService = Depends(get_agent_chat_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
    _rate: None = Depends(enforce_chat_limits),   # 회사당 분당·동시성·일일(429)
) -> AgentChatResponse:
    """대화 에이전트(RAG · Bedrock). 공고 검색·자격 판정을 대화로 답한다.

    company_id는 인증 토큰에서만 온다(요청 body 아님). 회사당 레이트리밋(분당·동시성·
    일일)→429, 응답 생성 중 재입력→409, 남의 세션(IDOR)→404, 에이전트 실패→502.
    """
    # ★ company_id는 토큰에서만 온다(요청 body 아님) — 멀티테넌시 격리의 신뢰 기준.
    try:
        session_id, resp = service.chat(
            query=payload.query,
            company_id=current_user.company_id,
            entry_bid_id=payload.entry_bid_id,
            session_id=payload.session_id,
        )
    except SessionBusyError:
        # 응답 생성 중 재입력 — 세션당 1턴(ADR-22). 프론트는 대기 UI로 막고, 뚫려도 여기서 방어.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이전 응답을 처리 중입니다. 잠시 후 다시 시도해주세요.",
        )
    except SessionForbiddenError:
        # 다른 회사 소유 세션(IDOR) — 존재를 숨겨 404로 통일.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="세션을 찾을 수 없습니다."
        )
    except Exception:
        # 에이전트 실패를 맨몸 500 대신 502로 규약화 — 프론트가 '일시 오류, 재시도'
        # UI를 만들 수 있게. 원인은 서버 로그로. 분리 이후엔 Bedrock 장애·스로틀뿐
        # 아니라 에이전트 프로세스 미기동(ConnectError)·응답 지연(Timeout)도 여기로
        # 모인다 — 어느 쪽이든 프론트가 할 일은 재시도라 같은 502로 둔다.
        logger.exception("agent chat failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="에이전트 응답에 실패했습니다. 잠시 후 다시 시도해주세요.",
        )
    return AgentChatResponse(
        session_id=session_id,
        action=resp.action,
        answer=resp.answer,
        clarify_message=resp.clarify_message,
        redirect_filters=resp.redirect_filters,
        citations=resp.citations,
    )
