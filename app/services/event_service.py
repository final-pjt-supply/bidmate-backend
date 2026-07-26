# -*- coding: utf-8 -*-
"""이벤트 수집 유스케이스.

event_name→event_type 파생, created_at(KST naive, 서버 수신 시각) 각인, company_id
주입(서버가 정함) 후 S3 sink에 넘긴다. 저장 형태는 S3(NDJSON, 날짜 파티션) —
분석을 운영 DB에서 분리하기 위함. HTTP 변환은 router, 적재는 infra.s3.event_sink.
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.event import EventIn
from app.domain.events import EVENT_TYPE_BY_NAME
from app.infra.s3.event_sink import S3EventSink

_KST = timezone(timedelta(hours=9))   # 한국 표준시(DST 없음).


class EventService:
    def __init__(self, sink: S3EventSink):
        self._sink = sink

    def record(self, payload: EventIn, *, company_id: int | None = None) -> None:
        """이벤트 한 건 적재.

        company_id는 서버(인증)가 정한다 — 라우터가 get_current_user(선택적 인증)로
        미리 정해 넘긴다. None이면 비로그인 이벤트.
        event_type은 클라 입력이 아니라 event_name에서 파생한다(drift 방지).
        created_at은 서버 수신 시각(KST naive) — 클라 시계를 믿지 않는다.
        """
        self._sink.add(
            {
                "company_id": company_id,
                "anonymous_id": payload.anonymous_id,
                "visit_id": str(payload.visit_id),
                "event_name": payload.event_name.value,
                "event_type": EVENT_TYPE_BY_NAME[payload.event_name].value,
                "page": payload.page,
                "referrer_page": payload.referrer_page,
                "bid_id": payload.bid_id,
                "chat_session_id": payload.chat_session_id,
                "query_log_id": payload.query_log_id,
                "properties": payload.properties,
                "device_type": payload.device_type,
                "app_version": payload.app_version,
                "created_at": datetime.now(_KST).replace(tzinfo=None).isoformat(timespec="seconds"),
            }
        )
