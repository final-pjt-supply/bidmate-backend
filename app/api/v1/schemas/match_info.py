# -*- coding: utf-8 -*-
"""MatchInfo — 매칭 판정 DTO. 별도 파일로 분리한 이유는 순환 임포트 때문이다.

match.py(GET /me/matches)는 bid.py의 BidListItem을 쓰고, bid.py(GET /bids/{bid_id})는
이 MatchInfo를 쓴다. MatchInfo를 match.py에 그대로 두면 bid.py ↔ match.py가
서로를 임포트하게 된다.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
