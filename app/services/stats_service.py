# -*- coding: utf-8 -*-
"""통계 화면 유스케이스.

집계는 이미 matview에 물질화돼 있으므로 여기서 하는 일은 두 가지뿐이다 —
조건 폴백과 응답 조립.
"""
from app.api.v1.schemas.stats import StatsConditions, StatsResponse
from app.domain.enums import StatsCategory
from app.infra.db.repositories.stats_repository import StatsRepository

# 업종 축과 품목 축 모두 "전체"를 같은 문자열로 쓴다(matview의 category/tag 값과 일치).
ALL = "ALL"


class StatsUnavailableError(RuntimeError):
    """matview가 비어 있다. 정상 배포에서는 나오지 않는다(0006이 WITH DATA로 만든다)."""


class StatsService:
    def __init__(self, repository: StatsRepository):
        self._repo = repository

    def get_stats(self, *, category: StatsCategory, tag: str | None) -> StatsResponse:
        """조건별 통계. 무효한 tag는 전체로 폴백한다.

        category는 폴백하지 않고 enum 검증(422)에 맡긴다. 클라이언트가 요청 전에
        알 수 있는 값이기 때문이다. tag는 다르다 — 고를 수 있는 품목 목록이 이
        응답 안에 들어 있어서, 요청을 보내기 전에는 유효성을 알 방법이 없다.
        그걸 404로 돌려주면 URL 하나 잘못 건드렸을 때 화면이 통째로 에러가 된다.
        """
        requested = (tag or "").strip() or ALL
        row = self._repo.get(category.value, requested)

        applied = requested
        if row is None and requested != ALL:
            row = self._repo.get(category.value, ALL)
            applied = ALL

        if row is None:
            raise StatsUnavailableError(category.value)

        payload, computed_at = row
        return StatsResponse(
            conditions=StatsConditions(
                category=category,
                # 화면 상태는 "전체"를 tag 없음으로 표현한다. 'ALL' 문자열을 그대로
                # 흘리면 프론트가 매번 되돌려야 한다.
                tag=None if applied == ALL else applied,
            ),
            computed_at=computed_at,
            **payload,
        )
