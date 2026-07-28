# -*- coding: utf-8 -*-
"""매칭 조회 유스케이스 — 사전계산 결과를 페이지로 내린다.

매칭은 계산하지 않는다(배치가 match_results에 precompute). 여기선 필터 정책
(merged·마감전)·페이지 계산·플랫 행 → 응답 조립만 한다.
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.bid import BidListItem
from app.api.v1.schemas.match import (
    MatchInfo,
    MatchListItem,
    MatchListResponse,
    MatchSummaryResponse,
)
from app.domain.enums import SearchSortKey
from app.infra.db.repositories.match_repository import MatchRepository

PAGE_SIZE = 20   # /bids 목록과 동일 계약
_KST = timezone(timedelta(hours=9))   # DB는 KST naive


class MatchService:
    def __init__(self, repository: MatchRepository):
        self._repo = repository

    def recompute_for_company(self, company_id: int) -> int:
        """그 회사 매칭을 재계산(전체 교체). 자격 저장 등 입력 변경 후 호출.
        반환은 적재된 행 수. 실패는 그대로 전파(호출자가 경계 처리)."""
        return self._repo.recompute_for_company(company_id)

    def list_matches(
        self,
        *,
        company_id: int,
        sort: SearchSortKey,
        page: int,
        include_infeasible: bool = False,
    ) -> MatchListResponse:
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        total = self._repo.count(
            company_id, clse_after=now_kst, include_infeasible=include_infeasible
        )
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_page(
            company_id,
            sort=sort.value,
            limit=PAGE_SIZE,
            offset=offset,
            clse_after=now_kst,
            include_infeasible=include_infeasible,
        )
        # 범위 밖 page는 rows=[]가 자연스럽게 나온다(200 + 빈 배열).
        items = [
            MatchListItem(
                bid=BidListItem.model_validate(bid),
                match=MatchInfo.model_validate(match),
            )
            for match, bid in rows
        ]
        return MatchListResponse(
            total=total, page=page, page_size=PAGE_SIZE, items=items
        )

    def summary(self, *, company_id: int) -> MatchSummaryResponse:
        """홈 대시보드 건수. 목록 없이 카운트만 센다."""
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        return MatchSummaryResponse(
            total=self._repo.count(company_id, clse_after=now_kst)
        )
