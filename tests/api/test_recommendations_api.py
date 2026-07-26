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
from app.services.recommendation_service import RecommendationService


def _bid(bid_id: str):
    return SimpleNamespace(
        bid_id=bid_id,
        bid_ntce_nm=f"{bid_id} 제목",
        dminstt_nm="수요기관",
        bid_category="servc",
        sucsfbid_mthd_nm=None,
        bid_clse_dt=datetime(2026, 8, 1),
        bdgt_amt=1_000_000,
        bid_prtcpt_lmt_yn=None,
        match_score=None,
    )


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
    def __init__(self, rows=None, queries=None, source="실적 계약명"):
        self.rows = rows if rows is not None else [
            (_match(), _bid("bid_a")),
            (_match("확인필요"), _bid("bid_b")),
        ]
        self.queries = queries if queries is not None else ["CCTV 설치공사"]
        self.source = source
        self.company_id = None

    def list_eligible_candidates(self, company_id, *, clse_after):
        self.company_id = company_id
        return self.rows

    def interest_queries(self, company_id):
        return self.queries, self.source


class FakeRecommendationSearch:
    def __init__(self):
        self.candidate_ids = []

    def embed(self, texts):
        return [[0.1, 0.2] for _ in texts]

    def search_titles(self, vector, *, candidate_ids, limit):
        self.candidate_ids = candidate_ids
        # rogue_bid는 검색 시스템이 잘못 돌려줘도 자격 후보 밖이므로 서비스가 버려야 한다.
        return [
            TitleSearchHit("bid_b", 0.81),
            TitleSearchHit("rogue_bid", 0.99),
            TitleSearchHit("bid_a", 0.72),
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
    client, repo, search = recommendation_client
    response = client.get("/me/recommendations?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_count"] == 2
    assert body["query_source"] == "실적 계약명"
    assert [item["bid"]["bid_id"] for item in body["items"]] == ["bid_b", "bid_a"]
    assert body["items"][0]["bid"]["match_score"] == 0.81
    assert body["items"][0]["recommendation"]["matched_text"] == "CCTV 설치공사"
    assert body["items"][0]["match"]["verdict"] == "확인필요"
    assert repo.company_id == 9001
    assert set(search.candidate_ids) == {"bid_a", "bid_b"}


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
        def search_titles(self, vector, *, candidate_ids, limit):
            return [
                TitleSearchHit("bid_c", 0.91),
                TitleSearchHit("bid_b", 0.90),
                TitleSearchHit("bid_a", 0.80),
            ]

    result = RecommendationService(repo, DuplicateSearch()).recommend(company_id=1, limit=3)
    assert [item.bid.bid_id for item in result.items] == ["bid_c", "bid_a"]


def test_no_interest_signal_returns_empty_without_search():
    repo = FakeRecommendationRepo(queries=[], source=None)

    class SearchMustNotRun:
        def embed(self, texts):
            raise AssertionError("관심 신호가 없으면 외부 검색을 호출하면 안 됩니다.")

    result = RecommendationService(repo, SearchMustNotRun()).recommend(
        company_id=1, limit=10
    )
    assert result.total == 0
    assert result.candidate_count == 2
    assert result.query_source is None


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
