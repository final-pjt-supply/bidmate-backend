# -*- coding: utf-8 -*-
"""user_events ORM — 고객 여정 이벤트(append-only).

API 서버 소유 테이블(Alembic 0002_user_events). 규약:
- append-only: INSERT만. updated_at 없음(수정할 일이 생기면 설계가 잘못된 것).
- soft ref(FK 없음): 로그 쓰기가 본 트랜잭션을 막지 않게 한다.
- created_at은 KST naive(서버 수신 시각). 앱이 각인한다(DB now()는 UTC라 안 씀).
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class UserEvent(Base):
    __tablename__ = "user_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 누가 (soft ref)
    company_id: Mapped[int | None] = mapped_column(BigInteger)   # 로그인 전 이벤트는 null
    anonymous_id: Mapped[str] = mapped_column(String(64))        # localStorage, 여정 연결 키

    # 어느 방문 (chat_sessions와 다른 개념 — 방문 세션)
    visit_id: Mapped[str] = mapped_column(UUID(as_uuid=False))   # 클라 생성, 30분 무활동 시 갱신

    # 무엇을
    event_name: Mapped[str] = mapped_column(String(60))
    event_type: Mapped[str] = mapped_column(String(20))          # event_name에서 서버가 파생

    # 어디서
    page: Mapped[str | None] = mapped_column(String(40))
    referrer_page: Mapped[str | None] = mapped_column(String(40))

    # 자주 조인하는 대상만 컬럼 승격 (soft ref)
    bid_id: Mapped[str | None] = mapped_column(String(60))
    chat_session_id: Mapped[int | None] = mapped_column(BigInteger)
    query_log_id: Mapped[int | None] = mapped_column(BigInteger)

    # 유연한 페이로드 / 환경
    properties: Mapped[dict | None] = mapped_column(JSONB)
    device_type: Mapped[str | None] = mapped_column(String(20))
    app_version: Mapped[str | None] = mapped_column(String(40))

    created_at: Mapped[datetime] = mapped_column(DateTime)       # KST naive, 서버 각인

    __table_args__ = (
        Index("idx_events_company_time", "company_id", "created_at"),
        Index("idx_events_anon_time", "anonymous_id", "created_at"),
        Index("idx_events_name_time", "event_name", "created_at"),
        Index("idx_events_visit_order", "visit_id", "created_at"),
        Index("idx_events_bid", "bid_id"),
    )
