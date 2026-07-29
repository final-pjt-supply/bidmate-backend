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
from app.services.recommendation_scoring import InterestSignal

_MERGED = QualStatus.MERGED.value
_INFEASIBLE = "불가"
# 출처별 상한. 한 종류가 임베딩 예산을 독점하지 않게 나눈다(전체 상한은 서비스가 건다).
_MAX_PER_SOURCE = 5
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

    def interest_signals(self, company_id: int) -> list[InterestSignal]:
        """관심 신호를 출처와 함께 '전부' 모은다.

        예전에는 사다리였다 — 스크랩이 있으면 스크랩만, 없으면 실적만 썼다. 그래서
        스크랩 하나를 지우면 추천이 면허 기준으로 통째로 뒤집혔고, 실적과 면허를
        같이 가진 회사도 한쪽만 반영됐다.

        지금은 다 모으고 약한 신호는 배수로 눌러(SIGNAL_WEIGHT) 지배력을 통제한다.
        면허명은 후보 대부분에 걸려 변별력이 낮지만, 다른 신호가 없는 회원에게는
        유일한 단서라 버리지 않는다.

        출처별 개수는 제한한다 — 한 종류가 임베딩 예산을 독점하지 않도록.
        """
        signals: list[InterestSignal] = []
        for source, texts in (
            ("스크랩 공고", self._scrap_titles(company_id)),
            ("실적 계약명", self._performance_names(company_id)),
            ("취급 품목", self._item_names(company_id)),
            ("보유 면허", self._license_names(company_id)),
        ):
            signals.extend(InterestSignal(text=t, source=source) for t in texts)
        return signals

    def license_codes(self, company_id: int) -> set[str]:
        """역량 축(면허 정합)용. 이름이 아니라 코드로 비교한다."""
        stmt = select(CompanyLicense.license_code).where(
            CompanyLicense.company_id == company_id,
            CompanyLicense.license_code.is_not(None),
        )
        return {str(c).strip() for c in self._session.scalars(stmt).all() if c}

    def performance_amounts(self, company_id: int) -> list[int]:
        """규모 축용 — 회사가 수행해 본 계약 금액."""
        stmt = select(CompanyPerformanceRecord.contract_amt).where(
            CompanyPerformanceRecord.company_id == company_id,
            CompanyPerformanceRecord.contract_amt.is_not(None),
        )
        return [int(a) for a in self._session.scalars(stmt).all() if a]

    def _scrap_titles(self, company_id: int) -> list[str]:
        stmt = (
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
            .limit(_MAX_PER_SOURCE)
        )
        return self._clean(self._session.scalars(stmt).all())

    def _performance_names(self, company_id: int) -> list[str]:
        stmt = (
            select(CompanyPerformanceRecord.contract_name)
            .where(
                CompanyPerformanceRecord.company_id == company_id,
                CompanyPerformanceRecord.contract_name.is_not(None),
            )
            .order_by(CompanyPerformanceRecord.end_date.desc().nulls_last())
            .limit(_MAX_PER_SOURCE)
        )
        return self._clean(self._session.scalars(stmt).all())

    def _item_names(self, company_id: int) -> list[str]:
        stmt = (
            select(CompanyItem.item_name)
            .where(
                CompanyItem.company_id == company_id,
                CompanyItem.item_name.is_not(None),
            )
            .limit(_MAX_PER_SOURCE)
        )
        return self._clean(self._session.scalars(stmt).all())

    def _license_names(self, company_id: int) -> list[str]:
        stmt = (
            select(CompanyLicense.license_name)
            .where(
                CompanyLicense.company_id == company_id,
                CompanyLicense.license_name.is_not(None),
            )
            .limit(_MAX_PER_SOURCE)
        )
        return self._clean(self._session.scalars(stmt).all())


    @staticmethod
    def _clean(values) -> list[str]:
        # 같은 제목/품목을 여러 번 임베딩하지 않는다. 입력 순서는 최근순을 보존한다.
        cleaned = (
            str(value).strip()
            for value in values
            if value is not None and str(value).strip()
        )
        return list(dict.fromkeys(cleaned))
