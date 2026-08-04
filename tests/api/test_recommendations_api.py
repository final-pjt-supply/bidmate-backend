# -*- coding: utf-8 -*-
"""추천 API/서비스 오프라인 계약 테스트."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_recommendation_service
from app.infra.search.recommendation_search import (
    RecommendationSearchError,
    TitleSearchHit,
)
from app.main import app
from app.services.recommendation_scoring import InterestSignal
from app.services.recommendation_service import RecommendationService


def _bid(bid_id: str, **overrides):
    base = dict(
        bid_id=bid_id,
        bid_ntce_nm=f"{bid_id} 제목",
        dminstt_nm="수요기관",
        bid_category="servc",
        sucsfbid_mthd_nm=None,
        # 벽시계에 의존하지 않게 먼 미래로 둔다(시의성 축이 마감일을 본다).
        bid_clse_dt=datetime(2027, 8, 1),
        bdgt_amt=1_000_000,
        presmpt_prce=1_000_000,
        bid_prtcpt_lmt_yn=None,
        required_licenses=None,
        match_score=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _match(verdict: str = "가능"):
    return SimpleNamespace(
        verdict=verdict,
        required=1,
        satisfied=1,
        gate_failed=0,
        need_review=0,
        axes=[],
        computed_at=datetime(2026, 7, 26),
    )


class FakeRecommendationRepo:
    def __init__(self, rows=None, queries=None, source="실적 계약명",
                 license_codes=None, perf_amounts=None):
        self.rows = rows if rows is not None else [
            (_match(), _bid("bid_a")),
            (_match("확인필요"), _bid("bid_b")),
        ]
        self.queries = queries if queries is not None else ["CCTV 설치공사"]
        self.source = source
        self._license_codes = license_codes or set()
        self._perf_amounts = perf_amounts or []
        self.company_id = None

    def list_eligible_candidates(self, company_id, *, clse_after):
        self.company_id = company_id
        return self.rows

    def interest_signals(self, company_id):
        return [InterestSignal(text=q, source=self.source) for q in self.queries]

    def license_codes(self, company_id):
        return self._license_codes

    def performance_amounts(self, company_id):
        return self._perf_amounts


class FakeRecommendationSearch:
    def __init__(self):
        self.candidate_ids = []

    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]

    def search_titles_many(self, vectors, *, candidate_ids, limit):
        self.candidate_ids = candidate_ids
        # rogue_bid는 검색 시스템이 잘못 돌려줘도 자격 후보 밖이므로 서비스가 버려야 한다.
        return [
            [
                TitleSearchHit("bid_b", 0.81),
                TitleSearchHit("rogue_bid", 0.99),
                TitleSearchHit("bid_a", 0.72),
            ]
            for _ in vectors
        ]


@pytest.fixture
def recommendation_client():
    repo = FakeRecommendationRepo()
    search = FakeRecommendationSearch()
    service = RecommendationService(repo, search)
    app.dependency_overrides[get_recommendation_service] = lambda: service
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
        company_id="9001", email=None
    )
    yield TestClient(app), repo, search
    app.dependency_overrides.clear()


def test_recommendation_contract_and_ranking(recommendation_client):
    """응답 계약과 정렬. 점수는 축 가중합이라 단일 유사도 값과 같지 않다."""
    client, repo, search = recommendation_client
    response = client.get("/me/recommendations?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_count"] == 2
    assert body["query_source"] == "실적 계약명"
    assert body["items"][0]["recommendation"]["matched_text"] == "CCTV 설치공사"
    assert repo.company_id == 9001
    assert set(search.candidate_ids) == {"bid_a", "bid_b"}
    # 점수는 0~1 가중합이고 내림차순이어야 한다.
    scores = [i["recommendation"]["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)
    # bid_a는 '가능', bid_b는 '확인필요' — 관심도가 같다면 자격 축이 순위를 가른다.
    assert [i["bid"]["bid_id"] for i in body["items"]] == ["bid_a", "bid_b"]


def test_bid_without_interest_hit_still_ranked():
    """검색에 안 걸린 공고도 자격·역량 축으로 순위에 들어온다.

    사다리 구조에서는 검색 히트가 없으면 추천에서 통째로 빠졌다. 스크랩·실적이
    없는 신규 회원이 아무 추천도 못 받던 원인이다.
    """
    repo = FakeRecommendationRepo(
        rows=[(_match("가능"), _bid("bid_a")), (_match("확인필요"), _bid("bid_b"))]
    )

    class NoHits(FakeRecommendationSearch):
        def search_titles_many(self, vectors, *, candidate_ids, limit):
            return [[] for _ in vectors]

    result = RecommendationService(repo, NoHits()).recommend(company_id=1, limit=10)
    assert result.total == 2                      # 둘 다 남는다
    assert result.items[0].bid.bid_id == "bid_a"  # '가능'이 위


def test_verdict_orders_when_interest_is_equal():
    """관심도가 동일하면 자격 판정이 순위를 가른다."""
    repo = FakeRecommendationRepo(
        rows=[(_match("확인필요"), _bid("bid_a")), (_match("가능"), _bid("bid_b"))]
    )

    class SameScore(FakeRecommendationSearch):
        def search_titles_many(self, vectors, *, candidate_ids, limit):
            return [[TitleSearchHit("bid_a", 0.8), TitleSearchHit("bid_b", 0.8)]
                    for _ in vectors]

    result = RecommendationService(repo, SameScore()).recommend(company_id=1, limit=10)
    assert result.items[0].bid.bid_id == "bid_b"   # 가능 > 확인필요


def test_license_match_lifts_ranking():
    """면허가 일치하는 공고가 위로 온다(역량 축)."""
    matching = _bid("bid_match", required_licenses=[{"code": "0037", "name_raw": "전기공사업"}])
    other = _bid("bid_other", required_licenses=[{"code": "9999", "name_raw": "기타"}])
    repo = FakeRecommendationRepo(
        rows=[(_match("확인필요"), other), (_match("확인필요"), matching)],
        license_codes={"0037"},
    )

    class SameScore(FakeRecommendationSearch):
        def search_titles_many(self, vectors, *, candidate_ids, limit):
            return [[TitleSearchHit("bid_match", 0.8), TitleSearchHit("bid_other", 0.8)]
                    for _ in vectors]

    result = RecommendationService(repo, SameScore()).recommend(company_id=1, limit=10)
    assert result.items[0].bid.bid_id == "bid_match"


def test_search_cannot_reintroduce_ineligible_bid(recommendation_client):
    client, _, _ = recommendation_client
    ids = [item["bid"]["bid_id"] for item in client.get("/me/recommendations").json()["items"]]
    assert "rogue_bid" not in ids


def test_duplicate_titles_are_collapsed():
    duplicate = _bid("bid_c")
    duplicate.bid_ntce_nm = "bid_b 제목"
    repo = FakeRecommendationRepo(
        rows=[(_match(), _bid("bid_a")), (_match(), _bid("bid_b")), (_match(), duplicate)]
    )

    class DuplicateSearch(FakeRecommendationSearch):
        def search_titles_many(self, vectors, *, candidate_ids, limit):
            return [
                [
                    TitleSearchHit("bid_c", 0.91),
                    TitleSearchHit("bid_b", 0.90),
                    TitleSearchHit("bid_a", 0.80),
                ]
                for _ in vectors
            ]

    result = RecommendationService(repo, DuplicateSearch()).recommend(company_id=1, limit=3)
    assert [item.bid.bid_id for item in result.items] == ["bid_c", "bid_a"]


def test_no_interest_signal_still_recommends_without_search():
    """관심 신호가 없어도 자격·역량 축으로 추천한다 — 외부 검색은 호출하지 않는다.

    예전에는 신호가 없으면 빈 목록을 돌려줬다(사다리의 끝). 스크랩도 실적도 없는
    신규 회원에게 아무것도 못 주던 상황이라, 지금은 나머지 축으로 순위를 만든다.
    임베딩할 텍스트가 없으므로 외부 검색 호출은 여전히 생략한다(비용·지연).
    """
    repo = FakeRecommendationRepo(queries=[], source=None)

    class SearchMustNotRun:
        def embed(self, texts):
            raise AssertionError("관심 신호가 없으면 외부 검색을 호출하면 안 됩니다.")

        def search_titles_many(self, vectors, *, candidate_ids, limit):
            raise AssertionError("관심 신호가 없으면 외부 검색을 호출하면 안 됩니다.")

    result = RecommendationService(repo, SearchMustNotRun()).recommend(
        company_id=1, limit=10
    )
    assert result.total == 2            # 빈 목록이 아니라 자격 순으로 나온다
    assert result.candidate_count == 2
    assert result.query_source is None  # 관심 신호 출처는 없음
    assert result.items[0].recommendation.signal_source == "회사 자격 정보"


def test_limit_validation(recommendation_client):
    client, _, _ = recommendation_client
    assert client.get("/me/recommendations?limit=0").status_code == 422
    assert client.get("/me/recommendations?limit=31").status_code == 422


def test_recommendations_require_login(recommendation_client):
    client, _, _ = recommendation_client
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).get("/me/recommendations").status_code == 401


def test_search_failure_is_503():
    repo = FakeRecommendationRepo()

    class BrokenSearch:
        def embed(self, texts):
            raise RecommendationSearchError("검색 준비 중")

        def search_titles_many(self, vectors, *, candidate_ids, limit):
            raise RecommendationSearchError("검색 준비 중")

    app.dependency_overrides[get_recommendation_service] = lambda: RecommendationService(
        repo, BrokenSearch()
    )
    app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
        company_id="9001", email=None
    )
    try:
        response = TestClient(app).get("/me/recommendations")
        assert response.status_code == 503
        assert response.json()["detail"] == "검색 준비 중"
    finally:
        app.dependency_overrides.clear()
