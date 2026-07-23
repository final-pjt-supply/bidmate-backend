# -*- coding: utf-8 -*-
"""이벤트 수집 유스케이스.

event_name→event_type 파생, created_at(KST naive, 서버 수신 시각) 각인, company_id
주입(서버가 정함) 후 저장한다. HTTP 변환은 router, 쓰기는 repository.
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.event import EventIn
from app.domain.events import EVENT_TYPE_BY_NAME
from app.infra.db.models.user_event import UserEvent
from app.infra.db.repositories.event_repository import EventRepository

_KST = timezone(timedelta(hours=9))   # 한국 표준시(DST 없음). DB는 KST naive.


class EventService:
    def __init__(self, repository: EventRepository):
        self._repo = repository

    def record(self, payload: EventIn, *, company_id: int | None = None) -> None:
        """이벤트 한 건 저장.

        company_id는 서버(인증)가 정한다 — 현재 인증 스텁이라 None(로그인 전 이벤트).
        인증(Phase 2)이 붙으면 라우터가 선택적 인증에서 company_id를 넘긴다.
        event_type은 클라 입력이 아니라 event_name에서 파생한다(drift 방지).
        """
        event = UserEvent(
            company_id=company_id,
            anonymous_id=payload.anonymous_id,
            visit_id=str(payload.visit_id),
            event_name=payload.event_name.value,
            event_type=EVENT_TYPE_BY_NAME[payload.event_name].value,
            page=payload.page,
            referrer_page=payload.referrer_page,
            bid_id=payload.bid_id,
            chat_session_id=payload.chat_session_id,
            query_log_id=payload.query_log_id,
            properties=payload.properties,
            device_type=payload.device_type,
            app_version=payload.app_version,
            created_at=datetime.now(_KST).replace(tzinfo=None),
        )
        self._repo.insert(event)
