# -*- coding: utf-8 -*-
"""회사별 매칭 조회 — match_results ⋈ bid_table.

노출 게이트(qual_status='merged')를 여기서 강제한다(bid_repository와 같은 불변식).
마감 지난 공고는 제외한다(추천/홈과 동일 규칙 — '지금 넣을 수 있는' 매칭만).
참가 불가 판정도 기본 제외한다(추천 목록이 불가로 채워지지 않게).
계산은 안 한다 — match_results는 배치가 채운 사전계산 결과다.
"""
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid
from app.infra.db.models.match_result import MatchResult

_MERGED = QualStatus.MERGED.value
_INFEASIBLE = "불가"

# match_results ⋈ bid_table 조인 조건 — count/list_page가 공유한다.
# (따로 쓰면 한쪽만 고쳐져 total과 목록이 어긋난다.)
_JOIN_ON = (Bid.bid_ntce_no == MatchResult.bid_ntce_no) & (
    Bid.bid_ntce_ord == MatchResult.bid_ntce_ord
)


class MatchRepository:
    def __init__(self, session: Session):
        self._session = session

    @staticmethod
    def _apply_filters(
        stmt, company_id: int, clse_after: datetime, include_infeasible: bool
    ):
        stmt = stmt.where(
            Bid.qual_status == _MERGED,
            MatchResult.company_id == company_id,
            # 마감 지난 공고 제외. 마감일 NULL은 "아직 안 닫힘"으로 보고 남긴다(공용 규칙).
            or_(Bid.bid_clse_dt >= clse_after, Bid.bid_clse_dt.is_(None)),
        )
        if not include_infeasible:
            # verdict NULL(판정 없음)은 남긴다 — 불가라고 단정할 근거가 없다.
            stmt = stmt.where(
                or_(MatchResult.verdict != _INFEASIBLE, MatchResult.verdict.is_(None))
            )
        return stmt

    def get_one(
        self, company_id: int, bid_ntce_no: str, bid_ntce_ord: str
    ) -> MatchResult | None:
        """공고 상세용 단건 조회. PK 조회라 merged·마감 필터가 필요 없다
        (그 판단은 이미 상세 화면에 도달했다는 것 자체가 의미한다 — BidService가
        get_by_bid_id에서 merged를 이미 검증한다)."""
        stmt = select(MatchResult).where(
            MatchResult.company_id == company_id,
            MatchResult.bid_ntce_no == bid_ntce_no,
            MatchResult.bid_ntce_ord == bid_ntce_ord,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def count(
        self, company_id: int, *, clse_after: datetime, include_infeasible: bool = False
    ) -> int:
        stmt = select(func.count()).select_from(MatchResult).join(Bid, _JOIN_ON)
        stmt = self._apply_filters(stmt, company_id, clse_after, include_infeasible)
        return self._session.execute(stmt).scalar_one()

    def list_page(
        self,
        company_id: int,
        *,
        sort: str,
        limit: int,
        offset: int,
        clse_after: datetime,
        include_infeasible: bool = False,
    ) -> list[tuple[MatchResult, Bid]]:
        """한 페이지의 (매칭, 공고) 쌍.

        마감 전만 노출하므로 정렬은 단순하다(활성/마감 버킷 불필요):
          * deadline: 마감 임박순 bid_clse_dt ASC + NULLS LAST
          * recent  : 최신 등록순 bid_ntce_dt DESC + NULLS LAST
        페이지 경계에서 행이 중복·누락되지 않게 bid_id로 tie-break.
        """
        stmt = select(MatchResult, Bid).join(Bid, _JOIN_ON)
        stmt = self._apply_filters(stmt, company_id, clse_after, include_infeasible)
        if sort == "recent":
            order = (Bid.bid_ntce_dt.desc().nulls_last(), Bid.bid_id.asc())
        else:
            order = (Bid.bid_clse_dt.asc().nulls_last(), Bid.bid_id.asc())
        stmt = stmt.order_by(*order).limit(limit).offset(offset)
        return [(row[0], row[1]) for row in self._session.execute(stmt).all()]
