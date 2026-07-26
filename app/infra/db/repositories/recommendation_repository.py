# -*- coding: utf-8 -*-
"""추천 후보와 회사 관심 텍스트 조회.

자격 판정은 여기서 끝낸다. OpenSearch 전체 검색 후 교집합하지 않고, 이 저장소가 만든
후보 bid_id만 검색 필터로 넘긴다.
"""
from datetime import datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid
from app.infra.db.models.company_profile import (
    CompanyItem,
    CompanyLicense,
    CompanyPerformanceRecord,
)
from app.infra.db.models.match_result import MatchResult
from app.infra.db.models.scrap import CompanyBidScrap

_MERGED = QualStatus.MERGED.value
_INFEASIBLE = "불가"
_MAX_INTEREST_QUERIES = 10
_JOIN_ON = (Bid.bid_ntce_no == MatchResult.bid_ntce_no) & (
    Bid.bid_ntce_ord == MatchResult.bid_ntce_ord
)


class RecommendationRepository:
    def __init__(self, session: Session):
        self._session = session

    def list_eligible_candidates(
        self, company_id: int, *, clse_after: datetime
    ) -> list[tuple[MatchResult, Bid]]:
        """자격 후보만 반환: merged, 미마감, 불가 아님, 제목 있음, 미스크랩."""
        is_scrapped = exists(
            select(1).where(
                CompanyBidScrap.company_id == company_id,
                CompanyBidScrap.bid_ntce_no == MatchResult.bid_ntce_no,
                CompanyBidScrap.bid_ntce_ord == MatchResult.bid_ntce_ord,
            )
        )
        stmt = (
            select(MatchResult, Bid)
            .join(Bid, _JOIN_ON)
            .where(
                MatchResult.company_id == company_id,
                Bid.qual_status == _MERGED,
                Bid.bid_ntce_nm.is_not(None),
                or_(Bid.bid_clse_dt >= clse_after, Bid.bid_clse_dt.is_(None)),
                or_(
                    MatchResult.verdict != _INFEASIBLE,
                    MatchResult.verdict.is_(None),
                ),
                ~is_scrapped,
            )
        )
        return [(row[0], row[1]) for row in self._session.execute(stmt).all()]

    def interest_queries(self, company_id: int) -> tuple[list[str], str | None]:
        """관심 신호 사다리. 상위 단계에 데이터가 있으면 아래 단계는 섞지 않는다."""
        scrap_stmt = (
            select(Bid.bid_ntce_nm)
            .join(
                CompanyBidScrap,
                (CompanyBidScrap.bid_ntce_no == Bid.bid_ntce_no)
                & (CompanyBidScrap.bid_ntce_ord == Bid.bid_ntce_ord),
            )
            .where(
                CompanyBidScrap.company_id == company_id,
                Bid.bid_ntce_nm.is_not(None),
            )
            .order_by(CompanyBidScrap.created_at.desc())
            .limit(_MAX_INTEREST_QUERIES)
        )
        scraps = self._clean(self._session.scalars(scrap_stmt).all())
        if scraps:
            return scraps, "스크랩 공고"

        perf_stmt = (
            select(CompanyPerformanceRecord.contract_name)
            .where(
                CompanyPerformanceRecord.company_id == company_id,
                CompanyPerformanceRecord.contract_name.is_not(None),
            )
            .order_by(CompanyPerformanceRecord.end_date.desc().nulls_last())
            .limit(_MAX_INTEREST_QUERIES)
        )
        performances = self._clean(self._session.scalars(perf_stmt).all())
        if performances:
            return performances, "실적 계약명"

        item_stmt = (
            select(CompanyItem.item_name)
            .where(
                CompanyItem.company_id == company_id,
                CompanyItem.item_name.is_not(None),
            )
            .limit(_MAX_INTEREST_QUERIES)
        )
        items = self._clean(self._session.scalars(item_stmt).all())
        if items:
            return items, "취급 품목"

        license_stmt = (
            select(CompanyLicense.license_name)
            .where(
                CompanyLicense.company_id == company_id,
                CompanyLicense.license_name.is_not(None),
            )
            .limit(_MAX_INTEREST_QUERIES)
        )
        licenses = self._clean(self._session.scalars(license_stmt).all())
        return (licenses, "보유 면허") if licenses else ([], None)

    @staticmethod
    def _clean(values) -> list[str]:
        # 같은 제목/품목을 여러 번 임베딩하지 않는다. 입력 순서는 최근순을 보존한다.
        cleaned = (
            str(value).strip()
            for value in values
            if value is not None and str(value).strip()
        )
        return list(dict.fromkeys(cleaned))
