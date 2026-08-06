# -*- coding: utf-8 -*-
"""자격 후보 안에서 제목 의미 유사도로 재정렬하는 추천 서비스."""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.bid import BidListItem
from app.api.v1.schemas.match_info import MatchInfo
from app.api.v1.schemas.recommendation import (
    RecommendationInfo,
    RecommendationListItem,
    RecommendationListResponse,
)
from app.infra.db.repositories.recommendation_repository import (
    RecommendationRepository,
)
from app.infra.search.recommendation_search import RecommendationSearch
from app.services.recommendation_scoring import (
    InterestSignal,
    ScoreBreakdown,
    score_bid,
)

_KST = timezone(timedelta(hours=9))
# 임베딩 비용·지연 상한. 출처별 5개씩 4종이면 최대 20개인데, 그중 상위만 쓴다.
_MAX_SIGNALS = 12
# 추천 이유에 노출할 축 이름
_AXIS_LABEL = {
    "interest": "관심사",
    "eligibility": "자격",
    "capability": "역량",
    "scale": "규모",
    "freshness": "일정",
}


def _reason(breakdown: ScoreBreakdown) -> str:
    """가장 높은 축 두 개로 추천 이유를 만든다.

    예전에는 "스크랩 공고와 제목이 유사합니다" 한 줄이라 왜 위에 있는지 알 수 없었다.
    점수를 만든 축을 그대로 보여주면 사용자가 납득하거나 반박할 수 있다.
    """
    top = sorted(breakdown.axes, key=lambda a: -a.score)[:2]
    parts = [f"{_AXIS_LABEL.get(a.key, a.key)}: {a.reason}" for a in top if a.reason]
    return " · ".join(parts) or "회사 조건과의 종합 점수"


class RecommendationService:
    def __init__(
        self,
        repository: RecommendationRepository,
        search: RecommendationSearch,
    ):
        self._repo = repository
        self._search = search

    def recommend(
        self, *, company_id: int, limit: int
    ) -> RecommendationListResponse:
        """자격 후보를 축별 점수의 가중 합으로 재정렬한다.

        관심도(벡터 유사도)만 쓰던 예전과 달리, 검색에 걸리지 않은 공고도 자격·역량·
        규모 축으로 순위에 들어온다. 그래서 스크랩·실적이 없는 신규 회원도 추천을
        받고, 신호 하나가 사라져도 순위가 통째로 뒤집히지 않는다.
        """
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        rows = self._repo.list_eligible_candidates(company_id, clse_after=now_kst)
        if not rows:
            return RecommendationListResponse(
                total=0, candidate_count=0, query_source=None, items=[]
            )

        # bid_id는 OpenSearch/RDS 간 불투명 공통키다. 같은 ID가 중복되면 첫 행만 쓴다.
        candidates = {bid.bid_id: (match, bid) for match, bid in rows}
        signals = self._repo.interest_signals(company_id)[:_MAX_SIGNALS]
        license_codes = self._repo.license_codes(company_id)
        perf_amounts = self._repo.performance_amounts(company_id)

        best_interest = self._best_interest(signals, list(candidates), limit)

        scored = []
        for bid_id, (match, bid) in candidates.items():
            breakdown = score_bid(
                bid=bid,
                match=match,
                interest=best_interest.get(bid_id),
                company_license_codes=license_codes,
                company_perf_amounts=perf_amounts,
                now=now_kst,
            )
            scored.append((breakdown.total, bid_id, breakdown))
        # 동점일 때 순서가 흔들리지 않게 bid_id로 2차 정렬한다.
        scored.sort(key=lambda x: (-x[0], x[1]))

        items = []
        seen_titles: set[str] = set()
        for total, bid_id, breakdown in scored:
            match, bid = candidates[bid_id]
            # 나라장터 정정 차수 때문에 bid_id는 달라도 제목이 같은 카드가 여러 장 생길
            # 수 있다. 추천 화면에서는 가장 높은 점수 한 장만 보여준다.
            title_key = (bid.bid_ntce_nm or "").strip().casefold()
            if title_key and title_key in seen_titles:
                continue
            if title_key:
                seen_titles.add(title_key)
            bid_item = BidListItem.model_validate(bid)
            bid_item.match_score = total
            items.append(
                RecommendationListItem(
                    bid=bid_item,
                    match=MatchInfo.model_validate(match),
                    recommendation=RecommendationInfo(
                        score=total,
                        reason=_reason(breakdown),
                        signal_source=breakdown.matched_source or "회사 자격 정보",
                        matched_text=breakdown.matched_text,
                    ),
                )
            )
            if len(items) >= limit:
                break

        primary = signals[0].source if signals else None
        return RecommendationListResponse(
            total=len(items),
            candidate_count=len(candidates),
            query_source=primary,
            items=items,
        )

    def _best_interest(
        self, signals: list[InterestSignal], candidate_ids: list[str], limit: int
    ) -> dict[str, tuple[float, InterestSignal]]:
        """공고별로 '가장 잘 맞은 관심 신호 하나'를 고른다.

        여러 신호가 같은 공고를 잡으면 최고점만 쓴다 — 신호 수가 많은 회사가
        합산으로 유리해지면 안 된다(그건 관심의 강도가 아니라 데이터 양이다).
        """
        if not signals:
            return {}
        # 상위 후보만 보면 관심도가 낮은 공고를 놓친다. 다른 축으로 올라올 수 있으므로
        # 검색 폭은 limit보다 넉넉히 잡는다.
        hits_by_signal = self._search.search_titles_many(
            self._search.embed([s.text for s in signals]),
            candidate_ids=candidate_ids,
            limit=max(limit * 3, 30),
        )
        best: dict[str, tuple[float, InterestSignal]] = {}
        # 후보 집합은 매 히트마다 같다 — 루프 안에서 set()을 다시 만들지 않는다.
        candidate_id_set = set(candidate_ids)
        for signal, hits in zip(signals, hits_by_signal):
            for hit in hits:
                if hit.bid_id not in candidate_id_set:
                    # 검색 filter 방어선. 외부 검색 응답을 신뢰해 자격 밖 공고를 섞지 않는다.
                    continue
                prev = best.get(hit.bid_id)
                # 배수를 적용한 뒤 비교해야 '약한 신호의 높은 원점수'가 이기지 않는다.
                if prev is None or hit.score * signal.weight > prev[0] * prev[1].weight:
                    best[hit.bid_id] = (hit.score, signal)
        return best
