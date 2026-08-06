# -*- coding: utf-8 -*-
"""추천 검색 어댑터 — 임베딩 캐시와 _msearch 동작(네트워크 없이)."""
import json

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


def test_clients_use_fast_connect_timeout():
    """외부(임베딩·OpenSearch) 호출이 죽은 호스트에 매달려 DB 커넥션을 물고 있지
    않도록 connect를 짧게(3s) 조인다 — 동기 요청은 외부 호출 내내 커넥션을 쥐므로,
    120초 매달림은 풀 소진→전체 API 캐스케이드로 번진다. read는 완만히(30s) 둔다
    (정상 kNN·임베딩엔 충분한 여유, 측정 후 더 조일 여지)."""
    search = _search()
    try:
        for client in (search._cf_client, search._os_client):
            assert client.timeout.connect == 3.0
            assert client.timeout.read == 30.0
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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _OpenSearchSpy:
    """_os_client.post 대역. 실제로 나간 요청을 붙잡아 둔다."""

    def __init__(self, payload):
        self._payload = payload
        self.url = None
        self.content = None
        self.headers = None

    def __call__(self, url, *, content=None, headers=None, **kwargs):
        self.url = url
        self.content = content
        self.headers = headers
        return _FakeResponse(self._payload)


def _hits(*bid_ids):
    return {
        "hits": {
            "hits": [
                {"_source": {"bid_id": bid_id}, "_score": 0.9 - i * 0.1}
                for i, bid_id in enumerate(bid_ids)
            ]
        }
    }


def _with_opensearch(payload):
    search = _search()
    spy = _OpenSearchSpy(payload)
    search._os_client.post = spy
    return search, spy


def test_msearch_sends_ndjson_header_and_body_pairs():
    search, spy = _with_opensearch({"responses": [_hits("a"), _hits("b")]})
    try:
        search.search_titles_many([[0.1], [0.2]], candidate_ids=["a", "b"], limit=12)
    finally:
        search.close()

    assert spy.url.endswith("/bid_chunks/_msearch")
    assert spy.headers["Content-Type"] == "application/x-ndjson"

    raw = spy.content.decode("utf-8")
    # NDJSON은 마지막 줄바꿈까지 있어야 OpenSearch가 받아준다.
    assert raw.endswith("\n")
    lines = raw.rstrip("\n").split("\n")
    # 검색 2건 → 헤더/본문 두 줄씩 4줄.
    assert len(lines) == 4
    assert lines[0] == "{}" and lines[2] == "{}"
    first = json.loads(lines[1])
    assert first["query"]["knn"]["vector"]["vector"] == [0.1]
    assert json.loads(lines[3])["query"]["knn"]["vector"]["vector"] == [0.2]
    # 후보 필터는 서브쿼리마다 실린다.
    assert first["query"]["knn"]["vector"]["filter"]["bool"]["filter"][0] == {
        "terms": {"bid_id": ["a", "b"]}
    }


def test_msearch_results_keep_request_order():
    search, _ = _with_opensearch({"responses": [_hits("first"), _hits("second")]})
    try:
        result = search.search_titles_many(
            [[0.1], [0.2]], candidate_ids=["first", "second"], limit=12
        )
    finally:
        search.close()

    assert [hit.bid_id for hit in result[0]] == ["first"]
    assert [hit.bid_id for hit in result[1]] == ["second"]


def test_sub_search_error_is_not_swallowed():
    """_msearch는 서브검색이 실패해도 HTTP 200이다. 결과 0건으로 넘어가면 안 된다."""
    search, _ = _with_opensearch(
        {"responses": [_hits("a"), {"error": {"type": "search_phase_execution_exception"}}]}
    )
    try:
        with pytest.raises(RecommendationSearchError):
            search.search_titles_many([[0.1], [0.2]], candidate_ids=["a"], limit=12)
    finally:
        search.close()


def test_response_count_mismatch_is_rejected():
    """개수가 어긋나면 엉뚱한 관심 쿼리에 결과가 붙는다."""
    search, _ = _with_opensearch({"responses": [_hits("a")]})
    try:
        with pytest.raises(RecommendationSearchError):
            search.search_titles_many([[0.1], [0.2]], candidate_ids=["a"], limit=12)
    finally:
        search.close()


def test_no_candidates_returns_empty_per_vector_without_calling_opensearch():
    search, spy = _with_opensearch({"responses": []})
    try:
        assert search.search_titles_many([[0.1], [0.2]], candidate_ids=[], limit=12) == [[], []]
    finally:
        search.close()
    assert spy.url is None


def test_no_vectors_skips_opensearch():
    search, spy = _with_opensearch({"responses": []})
    try:
        assert search.search_titles_many([], candidate_ids=["a"], limit=12) == []
    finally:
        search.close()
    assert spy.url is None


def test_cache_evicts_least_recently_used():
    cache = _EmbeddingCache(maxsize=2)
    cache.put_many([("a", [1.0]), ("b", [2.0])])
    cache.get_many(["a"])                  # a를 최근 사용으로 올린다
    cache.put_many([("c", [3.0])])         # 상한 초과 → 가장 오래된 b 축출

    assert set(cache.get_many(["a", "b", "c"])) == {"a", "c"}
