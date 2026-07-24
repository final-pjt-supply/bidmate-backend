# -*- coding: utf-8 -*-
"""POST /agent/chat 요청/응답 스키마.

응답 필드는 agents.schemas.AgentResponse(팀 공용 계약)를 미러링하되
session_context만 뺀다 — 왕복 규약(ADR 0005)상 컨텍스트는 서버(세션 스토어)가
보관하고 클라이언트에는 session_id만 준다. Filters/Citation은 계약 모델을
그대로 재사용한다(계약 문서가 곧 코드 — ADR 0005 결정 근거).
"""
from typing import Literal

from agents.schemas import Citation, Filters
from pydantic import BaseModel, Field


class AgentChatRequest(BaseModel):
    # 길이 제한은 비용 방어를 겸한다 — 빈/초장문 질의가 LLM(Bedrock)까지 가지 않게.
    query: str = Field(min_length=1, max_length=500)
    # 인증(Cognito JWT) 미구현 동안 프론트가 직접 보낸다. 인증이 붙으면
    # CurrentUser.company_id로 옮기고 이 필드는 제거한다(deps.py ★ 참고).
    company_id: str
    session_id: str | None = None
    entry_bid_id: str | None = None    # 특정 공고 상세 화면에서 진입 시(Case 2)


class AgentChatResponse(BaseModel):
    session_id: str
    action: Literal["answer", "redirect", "clarify"]
    answer: str | None = None
    clarify_message: str | None = None
    redirect_filters: Filters | None = None
    citations: list[Citation] = []
