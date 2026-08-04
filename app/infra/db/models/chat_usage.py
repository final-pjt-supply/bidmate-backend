# -*- coding: utf-8 -*-
"""챗 일일 호출 카운터 — 회사별 하루 호출 수(레이트리밋 일일 상한).

분당·동시성은 인메모리로 충분하지만(순간 상태), '일일'은 배포마다 리셋되면 안 되므로
지속 저장이 필요하다. 인메모리 일일 카운터는 하루에도 여러 번 하는 재배포에 매번 0이
돼 무의미하다. 그래서 RDS에 둔다. 다중 인스턴스/Lambda 전환 시 Redis로 이관.

(company_id, usage_date) 복합 PK — 회사×날짜당 한 행. UPSERT로 원자 증가한다.
날짜는 KST 기준(다른 일시 컬럼과 동일). 지난 날짜 행은 누적돼도 무해(원하면 정리).
"""
from datetime import date

from sqlalchemy import BigInteger, Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class ChatDailyUsage(Base):
    """회사×날짜별 챗 호출 수. 일일 상한 판정용."""
    __tablename__ = "chat_daily_usage"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
