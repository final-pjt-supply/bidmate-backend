# -*- coding: utf-8 -*-
"""스크랩 API 요청 스키마. 응답은 목록 계약을 공유하므로 bid.BidListResponse를 쓴다."""
from pydantic import BaseModel, Field


class ScrapCreate(BaseModel):
    """담기 요청. 프론트는 공고를 bid_id로 다루므로 그대로 받고,
    (bid_ntce_no, bid_ntce_ord) 분해는 서비스가 한다."""
    bid_id: str = Field(min_length=1, max_length=60)
