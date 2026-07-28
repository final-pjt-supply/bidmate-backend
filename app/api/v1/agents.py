# -*- coding: utf-8 -*-
"""POST /agent/chat — 대화 에이전트 진입점(ADR 0005: 라이브러리 임베드).

HTTP 경계만 담당한다 — 세션 왕복은 AgentChatService, 에이전트 로직은
bidmate-agents 패키지(run_agent) 소관. sync def라 FastAPI가 스레드풀에서
돌린다(run_agent는 Bedrock 동기 호출로 수 초 블로킹될 수 있다).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.chat_service import AgentChatService, SessionBusyError
from app.api.deps import get_agent_chat_service
from app.api.v1.schemas.agent import AgentChatRequest, AgentChatResponse
from app.infra.db.repositories.chat_repository import SessionForbiddenError

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
        # Bedrock 장애·스로틀 등 에이전트 실패를 맨몸 500 대신 502로 규약화 —
        # 프론트가 '일시 오류, 재시도' UI를 만들 수 있게. 원인은 서버 로그로.
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
