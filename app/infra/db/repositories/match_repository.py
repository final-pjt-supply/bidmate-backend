# -*- coding: utf-8 -*-
"""회사별 매칭 조회 — match_results ⋈ bid_table.

노출 게이트(qual_status='merged')를 여기서 강제한다(bid_repository와 같은 불변식).
마감 지난 공고는 제외한다(추천/홈과 동일 규칙 — '지금 넣을 수 있는' 매칭만).
계산은 안 한다 — match_results는 배치가 채운 사전계산 결과다.
"""
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid
from app.infra.db.models.match_result import MatchResult

_MERGED = QualStatus.MERGED.value


class MatchRepository:
    def __init__(self, session: Session):
        self._session = session

    def _join(self):
        """match_results ⋈ bid_table (복합키) + merged 게이트가 걸린 베이스."""
        return select(MatchResult, Bid).join(
            Bid,
            (Bid.bid_ntce_no == MatchResult.bid_ntce_no)
            & (Bid.bid_ntce_ord == MatchResult.bid_ntce_ord),
        ).where(Bid.qual_status == _MERGED)

    @staticmethod
    def _apply_filters(stmt, company_id: int, clse_after: datetime):
        stmt = stmt.where(MatchResult.company_id == company_id)
        # 마감 지난 공고 제외. 마감일 NULL은 "아직 안 닫힘"으로 보고 남긴다(공용 규칙).
        return stmt.where(
            or_(Bid.bid_clse_dt >= clse_after, Bid.bid_clse_dt.is_(None))
        )

    def count(self, company_id: int, *, clse_after: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(MatchResult)
            .join(
                Bid,
                (Bid.bid_ntce_no == MatchResult.bid_ntce_no)
                & (Bid.bid_ntce_ord == MatchResult.bid_ntce_ord),
            )
            .where(Bid.qual_status == _MERGED)
        )
        stmt = self._apply_filters(stmt, company_id, clse_after)
        return self._session.execute(stmt).scalar_one()

    def list_page(
        self,
        company_id: int,
        *,
        sort: str,
        limit: int,
        offset: int,
        clse_after: datetime,
    ) -> list[tuple[MatchResult, Bid]]:
        """한 페이지의 (매칭, 공고) 쌍.

        마감 전만 노출하므로 정렬은 단순하다(활성/마감 버킷 불필요):
          * deadline: 마감 임박순 bid_clse_dt ASC + NULLS LAST
          * recent  : 최신 등록순 bid_ntce_dt DESC + NULLS LAST
        페이지 경계에서 행이 중복·누락되지 않게 bid_id로 tie-break.
        """
        stmt = self._apply_filters(self._join(), company_id, clse_after)
        if sort == "recent":
            order = (Bid.bid_ntce_dt.desc().nulls_last(), Bid.bid_id.asc())
        else:
            order = (Bid.bid_clse_dt.asc().nulls_last(), Bid.bid_id.asc())
        stmt = stmt.order_by(*order).limit(limit).offset(offset)
        return [(row[0], row[1]) for row in self._session.execute(stmt).all()]
