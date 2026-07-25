# -*- coding: utf-8 -*-
"""GET /me/matches 응답 계약.

매칭 결과 = 공고정보(BidListItem 재사용 → 프론트가 BidCard 그대로) + 매칭정보.
원본값만 내린다 — verdict 한글변환·충족률 계산·D-day는 프론트 담당.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.bid import BidListItem


class MatchInfo(BaseModel):
    """사전계산 매칭 판정. axes는 배치가 넣은 축별 근거 원본(JSONB)."""
    model_config = ConfigDict(from_attributes=True)

    verdict: str | None = None          # 가능/불가/보완가능/확인필요
    required: int | None = None
    satisfied: int | None = None
    gate_failed: int | None = None
    need_review: int | None = None
    axes: list[dict] | None = None
    computed_at: datetime | None = None


class MatchListItem(BaseModel):
    bid: BidListItem
    match: MatchInfo


class MatchListResponse(BaseModel):
    total: int = Field(description="필터(merged·마감전) 적용 후 전체 매칭 건수")
    page: int
    page_size: int
    items: list[MatchListItem]
    # 0건 또는 범위 밖 page는 200 + items:[] (에러 아님)
