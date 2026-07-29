# -*- coding: utf-8 -*-
"""추천 검색 어댑터 — 임베딩 캐시 동작(네트워크 없이)."""
import pytest

from app.infra.search.recommendation_search import (
    RecommendationSearch,
    RecommendationSearchError,
    _EmbeddingCache,
)


def _search() -> RecommendationSearch:
    return RecommendationSearch(
        opensearch_url="https://opensearch.test",
        opensearch_user="u",
        opensearch_password="p",
        index_name="bid_chunks",
        verify_certs=False,
        cf_account_id="acct",
        cf_api_token="token",
        cf_model="@cf/baai/bge-m3",
    )


class _RemoteSpy:
    """_embed_remote 대역. 원격에 실제로 넘어간 텍스트만 기록한다."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t)), 0.5] for t in texts]


@pytest.fixture
def search_with_spy():
    search = _search()
    spy = _RemoteSpy()
    search._embed_remote = spy
    try:
        yield search, spy
    finally:
        search.close()


def test_repeated_texts_are_embedded_once(search_with_spy):
    """같은 관심 쿼리로 두 번 호출하면 두 번째는 원격 호출이 없다."""
    search, spy = search_with_spy
    first = search.embed(["CCTV 설치공사", "도로 포장공사"])
    second = search.embed(["CCTV 설치공사", "도로 포장공사"])

    assert first == second
    assert len(spy.calls) == 1
    assert spy.calls[0] == ["CCTV 설치공사", "도로 포장공사"]


def test_only_new_text_goes_remote(search_with_spy):
    """스크랩이 하나 늘면 늘어난 것만 임베딩한다."""
    search, spy = search_with_spy
    search.embed(["기존 공고"])
    result = search.embed(["기존 공고", "새로 스크랩한 공고"])

    assert spy.calls == [["기존 공고"], ["새로 스크랩한 공고"]]
    assert len(result) == 2
    assert result[0] == [float(len("기존 공고")), 0.5]


def test_order_is_preserved_when_cache_partially_hits(search_with_spy):
    """캐시 적중과 신규가 섞여도 입력 순서대로 반환한다."""
    search, spy = search_with_spy
    search.embed(["나"])
    result = search.embed(["가가가", "나", "다다"])

    assert result == [[3.0, 0.5], [1.0, 0.5], [2.0, 0.5]]
    assert spy.calls[-1] == ["가가가", "다다"]


def test_duplicate_texts_in_one_call_are_sent_once(search_with_spy):
    search, spy = search_with_spy
    result = search.embed(["같은 제목", "다른 제목", "같은 제목"])

    assert spy.calls == [["같은 제목", "다른 제목"]]
    assert result[0] == result[2]


def test_empty_input_skips_remote(search_with_spy):
    search, spy = search_with_spy
    assert search.embed([]) == []
    assert spy.calls == []


def test_short_remote_response_is_rejected(search_with_spy):
    """요청한 개수보다 적게 오면 엉뚱한 텍스트에 벡터가 붙지 않도록 막는다."""
    search, _ = search_with_spy
    search._embed_remote = lambda texts: [[0.1, 0.2]]
    with pytest.raises(RecommendationSearchError):
        search.embed(["하나", "둘"])


def test_missing_credentials_still_error():
    """캐시를 넣어도 설정 누락은 그대로 실패해야 한다."""
    search = RecommendationSearch(
        opensearch_url="https://opensearch.test",
        opensearch_user="",
        opensearch_password="",
        index_name="bid_chunks",
        verify_certs=False,
        cf_account_id="",
        cf_api_token="",
        cf_model="@cf/baai/bge-m3",
    )
    try:
        with pytest.raises(RecommendationSearchError):
            search.embed(["아무 텍스트"])
    finally:
        search.close()


def test_cache_evicts_least_recently_used():
    cache = _EmbeddingCache(maxsize=2)
    cache.put_many([("a", [1.0]), ("b", [2.0])])
    cache.get_many(["a"])                  # a를 최근 사용으로 올린다
    cache.put_many([("c", [3.0])])         # 상한 초과 → 가장 오래된 b 축출

    assert set(cache.get_many(["a", "b", "c"])) == {"a", "c"}
