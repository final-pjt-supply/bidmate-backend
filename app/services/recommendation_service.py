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

_KST = timezone(timedelta(hours=9))


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
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        rows = self._repo.list_eligible_candidates(company_id, clse_after=now_kst)
        queries, source = self._repo.interest_queries(company_id)
        if not rows or not queries or source is None:
            return RecommendationListResponse(
                total=0,
                candidate_count=len(rows),
                query_source=source,
                items=[],
            )

        # bid_id는 OpenSearch/RDS 간 불투명 공통키다. 같은 ID가 중복되면 첫 행만 쓴다.
        candidates = {bid.bid_id: (match, bid) for match, bid in rows}
        best: dict[str, tuple[float, str]] = {}
        # 관심 쿼리별 검색을 _msearch로 한 번에 보낸다. 쿼리마다 왕복하면 최대 10번의
        # 대기가 직렬로 쌓인다. 결과는 보낸 순서대로 돌아오므로 queries와 zip한다.
        vectors = self._search.embed(queries)
        hits_by_query = self._search.search_titles_many(
            vectors,
            candidate_ids=list(candidates),
            limit=limit,
        )
        for query, hits in zip(queries, hits_by_query):
            for hit in hits:
                if hit.bid_id not in candidates:
                    # 검색 filter 방어선. 외부 검색 응답을 신뢰해 자격 밖 공고를 섞지 않는다.
                    continue
                previous = best.get(hit.bid_id)
                if previous is None or hit.score > previous[0]:
                    best[hit.bid_id] = (hit.score, query)

        ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        items = []
        seen_titles: set[str] = set()
        for bid_id, (score, matched_text) in ranked:
            match, bid = candidates[bid_id]
            # 나라장터 정정 차수 때문에 bid_id는 달라도 제목이 같은 카드가 여러 장 생길
            # 수 있다. 추천 화면에서는 가장 높은 점수 한 장만 보여준다.
            title_key = (bid.bid_ntce_nm or "").strip().casefold()
            if title_key and title_key in seen_titles:
                continue
            if title_key:
                seen_titles.add(title_key)
            bid_item = BidListItem.model_validate(bid)
            bid_item.match_score = score
            items.append(
                RecommendationListItem(
                    bid=bid_item,
                    match=MatchInfo.model_validate(match),
                    recommendation=RecommendationInfo(
                        score=score,
                        reason=f"{source}과 공고 제목이 유사합니다.",
                        signal_source=source,
                        matched_text=matched_text,
                    ),
                )
            )
            if len(items) >= limit:
                break
        return RecommendationListResponse(
            total=len(items),
            candidate_count=len(candidates),
            query_source=source,
            items=items,
        )
