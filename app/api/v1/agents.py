# -*- coding: utf-8 -*-
"""POST /agent/chat — 대화 에이전트 진입점(ADR 0005: 라이브러리 임베드).

HTTP 경계만 담당한다 — 세션 왕복은 AgentChatService, 에이전트 로직은
bidmate-agents 패키지(run_agent) 소관. sync def라 FastAPI가 스레드풀에서
돌린다(run_agent는 Bedrock 동기 호출로 수 초 블로킹될 수 있다).
"""
from fastapi import APIRouter, Depends

from app.api.deps import get_agent_chat_service
from app.api.v1.schemas.agent import AgentChatRequest, AgentChatResponse
from app.services.agent_chat_service import AgentChatService

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=AgentChatResponse)
def chat(
    payload: AgentChatRequest,
    service: AgentChatService = Depends(get_agent_chat_service),
) -> AgentChatResponse:
    session_id, resp = service.chat(
        query=payload.query,
        company_id=payload.company_id,
        entry_bid_id=payload.entry_bid_id,
        session_id=payload.session_id,
    )
    return AgentChatResponse(
        session_id=session_id,
        action=resp.action,
        answer=resp.answer,
        clarify_message=resp.clarify_message,
        redirect_filters=resp.redirect_filters,
        citations=resp.citations,
    )
