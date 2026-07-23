# -*- coding: utf-8 -*-
"""이벤트 수집 요청 DTO. 클라(프론트)가 보내는 필드만 정의한다.

- company_id는 받지 않는다 — 신원은 서버가 인증에서 정한다(클라가 회사 사칭 못 하게).
- event_type도 받지 않는다 — event_name에서 서버가 파생한다.
- extra='forbid': 모르는 필드는 거부(오타·규격 외 데이터·PII 유입 방지).
"""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.events import EventName


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anonymous_id: str = Field(max_length=64, description="localStorage 익명 식별자(여정 키)")
    visit_id: UUID = Field(description="방문 세션(클라 생성 UUID)")
    event_name: EventName
    page: str | None = Field(default=None, max_length=40)
    referrer_page: str | None = Field(default=None, max_length=40)
    bid_id: str | None = Field(default=None, max_length=60)
    chat_session_id: int | None = None
    query_log_id: int | None = None
    properties: dict | None = None
    device_type: str | None = Field(default=None, max_length=20)
    app_version: str | None = Field(default=None, max_length=40)
