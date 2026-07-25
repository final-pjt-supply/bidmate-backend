# -*- coding: utf-8 -*-
"""company_bid_scraps ORM 매핑 — 회원이 담은 공고(스크랩).

회사↔공고 다대다 연결 테이블. match_results와 같은 복합키 패턴이다
(company_id + 공고 PK). 공고는 bid_id가 아니라 (bid_ntce_no, bid_ntce_ord)로
가리킨다 — bid_table의 PK가 그 둘이고 bid_id에는 유니크 제약이 없어 FK를 걸 수 없다.

⚠ 이 ORM은 공유 RDS 스키마의 '반영'이지 원본이 아니다 — 테이블 생성/변경은
팀 협의 + Alembic으로만 한다. 이 앱은 create_all을 부르지 않는다.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class CompanyBidScrap(Base):
    __tablename__ = "company_bid_scraps"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bid_ntce_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    bid_ntce_ord: Mapped[str] = mapped_column(String(10), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
