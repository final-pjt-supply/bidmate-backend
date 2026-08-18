# -*- coding: utf-8 -*-
"""취소 제외 게이트(#140) — 컴파일된 SQL에 실제로 박히는지 DB 없이 고정한다.

실제 행 동작(원 공고까지 숨는지)은 test_bid_repository_integration.py의
test_canceled_notice_hides_all_ords가 로컬 docker Postgres에서 검증한다. 여기서는
그 DB가 없는 환경(CI 등)에서도, 목록·검색·매칭·추천의 쿼리에서 누군가 게이트를
빼먹으면 즉시 깨지도록 컴파일 결과만 확인한다.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.infra.db.models.bid import Bid
from app.infra.db.models.match_result import MatchResult
from app.infra.db.repositories.bid_repository import BidRepository, not_canceled_clause
from app.infra.db.repositories.match_repository import MatchRepository

_NOW = datetime(2026, 8, 13, 12, 0)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _assert_gate(sql: str) -> None:
    # 게이트 형태: NOT EXISTS(같은 bid_ntce_no에 ntce_kind_nm='취소공고' 행)
    assert "NOT (EXISTS" in sql
    assert "ntce_kind_nm" in sql


def test_clause_shape():
    sql = _sql(select(Bid).where(not_canceled_clause()))
    _assert_gate(sql)
    # 상관 서브쿼리 — 외부 Bid와 공고번호로 묶여야 공고번호 전체가 함께 숨는다.
    assert "bid_ntce_no = bid_table.bid_ntce_no" in sql


def test_bid_repository_paths_have_gate():
    repo = BidRepository(session=None)
    _assert_gate(_sql(repo._merged_base()))


def test_match_repository_filter_has_gate():
    stmt = select(MatchResult, Bid).join(
        Bid,
        (Bid.bid_ntce_no == MatchResult.bid_ntce_no)
        & (Bid.bid_ntce_ord == MatchResult.bid_ntce_ord),
    )
    stmt = MatchRepository._apply_filters(
        stmt, company_id=1, clse_after=_NOW, include_infeasible=False
    )
    _assert_gate(_sql(stmt))


def test_recommendation_candidates_have_gate():
    from app.infra.db.repositories.recommendation_repository import (
        RecommendationRepository,
    )

    class _Capture:
        def execute(self, stmt):
            self.stmt = stmt
            return _Empty()

    class _Empty:
        def all(self):
            return []

    session = _Capture()
    RecommendationRepository(session).list_eligible_candidates(1, clse_after=_NOW)
    _assert_gate(_sql(session.stmt))
