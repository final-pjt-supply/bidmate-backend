# -*- coding: utf-8 -*-
"""match_results ORM 매핑 — 회사×공고 사전계산 매칭 결과(자격 판정).

매칭 계산은 DB측 배치(normalizer)가 돌려 이 테이블에 적재한다 — 백엔드는 읽기만
한다(create_all 안 함, alembic 제외 = coexist). 공고는 bid_id가 아니라
(bid_ntce_no, bid_ntce_ord)로 가리킨다(bid_table PK, bid_id엔 유니크 제약 없음).

PK=(company_id, bid_ntce_no, bid_ntce_ord) — 회사×공고 1행.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class MatchResult(Base):
    __tablename__ = "match_results"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bid_ntce_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    bid_ntce_ord: Mapped[str] = mapped_column(String(10), primary_key=True)

    verdict: Mapped[str | None] = mapped_column(String(20))   # 가능/불가/보완가능/확인필요
    required: Mapped[int | None] = mapped_column(SmallInteger)
    satisfied: Mapped[int | None] = mapped_column(SmallInteger)
    gate_failed: Mapped[int | None] = mapped_column(SmallInteger)
    need_review: Mapped[int | None] = mapped_column(SmallInteger)
    axes: Mapped[list | None] = mapped_column(JSONB)          # 축별 근거([{axis,class,detail,status}])
    normalizer_version: Mapped[str | None] = mapped_column(String(20))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime)
