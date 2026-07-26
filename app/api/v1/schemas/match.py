# -*- coding: utf-8 -*-
"""GET /me/matches 응답 계약.

매칭 결과 = 공고정보(BidListItem 재사용 → 프론트가 BidCard 그대로) + 매칭정보.
원본값만 내린다 — verdict 한글변환·충족률 계산·D-day는 프론트 담당.
"""
from pydantic import BaseModel, Field

from app.api.v1.schemas.bid import BidListItem
from app.api.v1.schemas.match_info import MatchInfo


class MatchListItem(BaseModel):
    bid: BidListItem
    match: MatchInfo


class MatchSummaryResponse(BaseModel):
    """홈 대시보드용 건수 요약. 목록을 받아 total만 쓰는 낭비를 피하려고 따로 둔다."""

    total: int = Field(description="참가 가능한(불가 제외) 마감 전 매칭 건수")


class MatchListResponse(BaseModel):
    total: int = Field(description="필터(merged·마감전) 적용 후 전체 매칭 건수")
    page: int
    page_size: int
    items: list[MatchListItem]
    # 0건 또는 범위 밖 page는 200 + items:[] (에러 아님)
