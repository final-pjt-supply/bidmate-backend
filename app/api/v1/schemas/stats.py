# -*- coding: utf-8 -*-
"""통계 화면 응답 계약(DTO).

bid_stats matview의 payload jsonb 모양을 그대로 반영한다. 원본값만 내린다 —
1~12월 축 채우기, 금액 포맷, "가장 많아요" 문장은 프론트 담당이다.

한 조건(업종 x 품목) 분량만 내려간다. 전 조건을 담은 덩어리를 주고 프론트가
거르게 하면 응답이 253KB가 된다(목업이 그랬다). 조건당 3KB 남짓.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import StatsCategory


class StatsPeriod(BaseModel):
    """집계 기간. 진행 중인 달은 빠져 있다."""
    model_config = ConfigDict(populate_by_name=True)

    # 'from'은 파이썬 예약어라 필드명으로 못 쓴다. 응답 키는 alias로 'from'이 나간다.
    from_: str = Field(alias="from", description="YYYY-MM")
    to: str = Field(description="YYYY-MM")


class StatsConditions(BaseModel):
    """실제로 적용된 조건. 요청과 다를 수 있다 — 무효한 tag는 전체로 폴백한다."""
    category: StatsCategory
    tag: str | None = None


class TagCount(BaseModel):
    tag: str
    cnt: int


class MonthCount(BaseModel):
    m: str = Field(description="YYYY-MM")
    cnt: int


class BudgetBucket(BaseModel):
    """추정가격 구간. 경계는 실무 기준선이다
    (0: 5천만 미만 / 1: ~2억 소액수의 / 2: ~10억 적격심사 / 3: ~50억 / 4: 50억 이상)."""
    b: int = Field(ge=0, le=4)
    cnt: int


class BudgetDistribution(BaseModel):
    buckets: list[BudgetBucket] = []
    # 구간 합. total(헤더 총계)과 다를 수 있다 — 전체 조건에서는 금액 정보가 없는
    # 업종이 여기서만 빠진다. 프론트는 %의 분모로 이 값을 써야 한다.
    total: int = 0


class InstitutionCount(BaseModel):
    name: str
    cnt: int


class StatsResponse(BaseModel):
    conditions: StatsConditions
    period: StatsPeriod
    computed_at: datetime = Field(description="matview 갱신 시각. 신선도 판단용.")

    total: int = Field(description="조건에 해당하는 공고 수(헤더 총계)")
    # 금액 정보가 아예 안 들어오는 업종이 있다(외자). false면 예산 분포를 그리면 안 된다 —
    # 0을 사실로 그리면 "전부 5천만 미만 100%"라는 거짓이 화면에 뜬다.
    amount_available: bool

    tags: list[TagCount] = Field(default=[], description="선택된 업종의 품목 칩 상위 8개")
    monthly: list[MonthCount] = Field(default=[], description="관측된 달만. 빈 달은 cnt 0")
    budget: BudgetDistribution
    institutions: list[InstitutionCount] = Field(
        default=[], description="발주 건수 상위 8개. '각 수요기관'은 제외됨"
    )
    excluded_mas: int = Field(
        default=0,
        description="순위에서 뺀 '각 수요기관'(다수공급자·제3자단가계약) 건수",
    )
