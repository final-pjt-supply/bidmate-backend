# -*- coding: utf-8 -*-
"""챗봇 대화 조회 응답 스키마 — 내 세션 목록 / 세션 상세(메시지).

session_id는 uuid지만 클라에는 문자열로 준다(서비스가 str로 변환). 대화 이력을
'되읽는' 조회 계약이다(쓰기는 /agent/chat이 담당)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionListItem(BaseModel):
    session_id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SessionListItem]
    # 0건·범위밖 page는 200 + items:[]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str                       # user / assistant
    content: str | None = None
    response_meta: dict | None = None   # assistant: {action, citations, redirect_filters}
    created_at: datetime


class SessionDetailResponse(BaseModel):
    session_id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageOut]
