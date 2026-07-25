# -*- coding: utf-8 -*-
"""매칭 조회 유스케이스 — 사전계산 결과를 페이지로 내린다.

매칭은 계산하지 않는다(배치가 match_results에 precompute). 여기선 필터 정책
(merged·마감전)·페이지 계산·플랫 행 → 응답 조립만 한다.
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.bid import BidListItem
from app.api.v1.schemas.match import MatchInfo, MatchListItem, MatchListResponse
from app.domain.enums import SearchSortKey
from app.infra.db.repositories.match_repository import MatchRepository

PAGE_SIZE = 20   # /bids 목록과 동일 계약
_KST = timezone(timedelta(hours=9))   # DB는 KST naive


class MatchService:
    def __init__(self, repository: MatchRepository):
        self._repo = repository

    def list_matches(
        self, *, company_id: int, sort: SearchSortKey, page: int
    ) -> MatchListResponse:
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        total = self._repo.count(company_id, clse_after=now_kst)
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_page(
            company_id,
            sort=sort.value,
            limit=PAGE_SIZE,
            offset=offset,
            clse_after=now_kst,
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
