# -*- coding: utf-8 -*-
"""GET /me/recommendations 응답 계약."""
from pydantic import BaseModel, Field

from app.api.v1.schemas.bid import BidListItem
from app.api.v1.schemas.match_info import MatchInfo


class RecommendationInfo(BaseModel):
    """관심도 점수와 설명. 자격 판정(match)과 의도적으로 분리한다."""

    score: float = Field(description="제목 벡터 검색 점수. 가중치 캘리브레이션 전 원점수")
    reason: str
    signal_source: str = Field(description="스크랩/실적/품목/면허 중 사용한 관심 신호")
    matched_text: str = Field(description="가장 높은 점수를 만든 회사 관심 텍스트")


class RecommendationListItem(BaseModel):
    bid: BidListItem
    match: MatchInfo
    recommendation: RecommendationInfo


class RecommendationListResponse(BaseModel):
    total: int = Field(description="이번 요청에서 반환한 추천 건수")
    candidate_count: int = Field(description="불가·마감·스크랩을 제외한 자격 후보 건수")
    query_source: str | None = Field(description="추천에 사용한 관심 신호 단계")
    items: list[RecommendationListItem]
