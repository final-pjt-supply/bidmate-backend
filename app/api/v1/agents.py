# -*- coding: utf-8 -*-
"""POST /agent/chat — 대화 에이전트 진입점.

HTTP 경계만 담당한다 — 세션 왕복은 AgentChatService, 에이전트 로직은 별도
서비스(루프백 8010의 POST /turn) 소관. sync def라 FastAPI가 스레드풀에서
돌린다(에이전트 호출은 검색+Bedrock 합성이라 수 초~수십 초 블로킹된다).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.chat_service import AgentChatService
from app.api.deps import get_agent_chat_service
from app.api.v1.schemas.agent import AgentChatRequest, AgentChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse)
def chat(
    payload: AgentChatRequest,
    service: AgentChatService = Depends(get_agent_chat_service),
) -> AgentChatResponse:
    try:
        session_id, resp = service.chat(
            query=payload.query,
            company_id=payload.company_id,
            entry_bid_id=payload.entry_bid_id,
            session_id=payload.session_id,
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
