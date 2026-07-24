# -*- coding: utf-8 -*-
"""GET /bids/search 계약 테스트.

검색 전용 경로는 홈·추천이 쓰는 GET /bids와 분리돼 있다. 여기서는 정렬이 실제로
결과를 바꾸는지, 라우트 우선순위(/search가 /{bid_id}에 먹히지 않는지), 그리고
기존 경로가 영향을 받지 않았는지를 고정한다.
"""
from datetime import datetime

from tests.api.conftest import make_bid


def _rows():
    """마감일과 게시일 순서를 일부러 엇갈리게 둔 3건 — 정렬이 바뀌면 순서도 바뀐다."""
    return [
        make_bid(
            bid_id="a_00", bid_ntce_no="a",
            bid_clse_dt=datetime(2027, 3, 1), bid_ntce_dt=datetime(2026, 1, 1),
        ),
        make_bid(
            bid_id="b_00", bid_ntce_no="b",
            bid_clse_dt=datetime(2027, 1, 1), bid_ntce_dt=datetime(2026, 2, 1),
        ),
        make_bid(
            bid_id="c_00", bid_ntce_no="c",
            bid_clse_dt=datetime(2027, 2, 1), bid_ntce_dt=datetime(2026, 3, 1),
        ),
    ]


def test_search_returns_same_shape_as_list(client_with_rows):
    client = client_with_rows([make_bid()])
    res = client.get("/bids/search")
    assert res.status_code == 200
    body = res.json()
    # 응답 계약은 GET /bids와 동일해야 한다(프론트가 같은 타입을 쓴다).
    assert set(body) == {"total", "page", "page_size", "items"}
    assert body["page_size"] == 20
    assert set(body["items"][0]) == {
        "bid_id", "bid_ntce_nm", "dminstt_nm", "bid_category",
        "sucsfbid_mthd_nm", "bid_clse_dt", "bdgt_amt",
        "bid_prtcpt_lmt_yn", "match_score",
    }


def test_default_sort_is_deadline(client_with_rows):
    client = client_with_rows(_rows())
    ids = [i["bid_id"] for i in client.get("/bids/search").json()["items"]]
    assert ids == ["b_00", "c_00", "a_00"]   # 마감 빠른 순


def test_sort_recent_orders_by_notice_date_desc(client_with_rows):
    client = client_with_rows(_rows())
    ids = [i["bid_id"] for i in client.get("/bids/search?sort=recent").json()["items"]]
    assert ids == ["c_00", "b_00", "a_00"]   # 게시일 늦은 순 — deadline과 순서가 다르다


def test_sort_score_is_rejected(client_with_rows):
    """score는 /recommend 전용이다. 검색 경로에서는 계약상 받지 않는다."""
    client = client_with_rows(_rows())
    assert client.get("/bids/search?sort=score").status_code == 422


def test_invalid_sort_is_422(client_with_rows):
    client = client_with_rows(_rows())
    assert client.get("/bids/search?sort=nope").status_code == 422


def test_category_filter(client_with_rows):
    rows = [
        make_bid(bid_id="s_00", bid_ntce_no="s", bid_category="servc"),
        make_bid(bid_id="w_00", bid_ntce_no="w", bid_category="cnstwk"),
    ]
    client = client_with_rows(rows)
    body = client.get("/bids/search?category=cnstwk").json()
    assert body["total"] == 1
    assert body["items"][0]["bid_id"] == "w_00"


def test_excludes_non_merged(client_with_rows):
    rows = [
        make_bid(bid_id="ok_00", bid_ntce_no="ok", qual_status="merged"),
        make_bid(bid_id="no_00", bid_ntce_no="no", qual_status="pending"),
    ]
    body = client_with_rows(rows).get("/bids/search").json()
    assert body["total"] == 1 and body["items"][0]["bid_id"] == "ok_00"


def test_excludes_closed_but_keeps_null_deadline(client_with_rows):
    """마감 지난 건 제외하되 마감일 NULL은 남긴다(공용 경로와 동일 규칙)."""
    rows = [
        make_bid(bid_id="past_00", bid_ntce_no="past", bid_clse_dt=datetime(2020, 1, 1)),
        make_bid(bid_id="null_00", bid_ntce_no="null", bid_clse_dt=None),
        make_bid(bid_id="fut_00", bid_ntce_no="fut", bid_clse_dt=datetime(2027, 1, 1)),
    ]
    ids = {i["bid_id"] for i in client_with_rows(rows).get("/bids/search").json()["items"]}
    assert ids == {"null_00", "fut_00"}


def test_page_out_of_range_returns_empty_not_error(client_with_rows):
    client = client_with_rows(_rows())
    body = client.get("/bids/search?page=99").json()
    assert body["total"] == 3 and body["items"] == []


def test_search_route_not_shadowed_by_detail_route(client_with_rows):
    """/bids/search가 /bids/{bid_id}에 먹히면 404가 난다 — 라우트 순서 회귀 방지."""
    client = client_with_rows([make_bid()])
    assert client.get("/bids/search").status_code == 200


def test_list_endpoint_still_rejects_recent(client_with_rows):
    """기존 GET /bids의 정렬 계약은 그대로 — recent는 여기서 받지 않는다."""
    client = client_with_rows([make_bid()])
    assert client.get("/bids?sort=recent").status_code == 422
    assert client.get("/bids?sort=deadline").status_code == 200
    assert client.get("/bids?sort=score").status_code == 200
