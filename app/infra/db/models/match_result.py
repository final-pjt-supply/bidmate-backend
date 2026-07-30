# -*- coding: utf-8 -*-
"""match_results ORM 매핑 — 회사×공고 사전계산 매칭 결과(자격 판정).

매칭 계산은 DB 함수 `compute_match_results(company_id)`가 돌린다 — 백엔드는 그 결과를
이 테이블에 적재(recompute/refresh)하고 읽는다. DDL은 안 낸다(create_all 안 함,
alembic 제외 = coexist). 공고는 bid_id가 아니라 (bid_ntce_no, bid_ntce_ord)로
가리킨다(bid_table PK, bid_id엔 유니크 제약 없음).

PK=(company_id, bid_ntce_no, bid_ntce_ord) — 회사×공고 1행.

컬럼 계약(§2-A, 5차 배포에도 불변): compute_match_results가 RETURNS TABLE로 10컬럼을
항상 채운다 — verdict(CASE라 항상 값)·required·satisfied·gate_failed·need_review(정수
카운트)·axes(근거 배열)·normalizer_version. 그래서 non-PK 컬럼은 NOT NULL로 둔다(D-14).
computed_at은 적재 시 기본값(now KST)이라 항상 채워진다. coexist라 nullable 플래그는
DDL이 아니라 매핑 메타데이터·타입 정확성용이다.
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

    verdict: Mapped[str] = mapped_column(String(20))   # 가능/불가/보완가능/확인필요
    required: Mapped[int] = mapped_column(SmallInteger)
    satisfied: Mapped[int] = mapped_column(SmallInteger)
    gate_failed: Mapped[int] = mapped_column(SmallInteger)
    need_review: Mapped[int] = mapped_column(SmallInteger)
    axes: Mapped[list] = mapped_column(JSONB)          # 축별 근거([{axis,class,detail,status}])
    normalizer_version: Mapped[str] = mapped_column(String(20))
    computed_at: Mapped[datetime] = mapped_column(DateTime)
