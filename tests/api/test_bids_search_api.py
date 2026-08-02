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
    # 검색은 24 — 프론트 목록이 3열 그리드라 20이면 마지막 줄이 어긋난다.
    assert body["page_size"] == 24
    assert set(body["items"][0]) == {
        "bid_id", "bid_ntce_nm", "dminstt_nm", "bid_category",
        "sucsfbid_mthd_nm", "bid_clse_dt", "bdgt_amt",
        "bid_prtcpt_lmt_yn", "item_tag", "match_score",
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


def _q_rows():
    """검색어가 걸릴 컬럼을 하나씩 다르게 둔 3건 + 아무 데도 안 걸리는 1건."""
    return [
        make_bid(bid_id="nm_00", bid_ntce_no="nm", bid_ntce_nm="도로 포장공사",
                 dminstt_nm="성남시청", ntce_instt_nm="조달청"),
        make_bid(bid_id="dm_00", bid_ntce_no="dm", bid_ntce_nm="교량 보수",
                 dminstt_nm="한국도로공사", ntce_instt_nm="조달청"),
        make_bid(bid_id="nt_00", bid_ntce_no="nt", bid_ntce_nm="상수도 정비",
                 dminstt_nm="성남시청", ntce_instt_nm="도로관리사업소"),
        make_bid(bid_id="no_00", bid_ntce_no="no", bid_ntce_nm="전산장비 구매",
                 dminstt_nm="성남시청", ntce_instt_nm="조달청"),
    ]


def test_q_matches_any_of_three_columns(client_with_rows):
    """공고명·수요기관·공고기관 중 하나만 걸려도 결과에 포함된다."""
    client = client_with_rows(_q_rows())
    body = client.get("/bids/search?q=도로").json()
    assert body["total"] == 3
    assert {i["bid_id"] for i in body["items"]} == {"nm_00", "dm_00", "nt_00"}


def test_q_is_case_insensitive(client_with_rows):
    rows = [make_bid(bid_id="en_00", bid_ntce_no="en", bid_ntce_nm="AI 플랫폼 구축")]
    client = client_with_rows(rows)
    assert client.get("/bids/search?q=ai").json()["total"] == 1


def test_q_absent_or_blank_means_no_filter(client_with_rows):
    """공백만 친 검색어로 결과가 사라지면 안 된다(패턴이 '%   %'가 되는 실수 방지)."""
    client = client_with_rows(_q_rows())
    assert client.get("/bids/search").json()["total"] == 4
    assert client.get("/bids/search?q=").json()["total"] == 4
    assert client.get("/bids/search?q=%20%20").json()["total"] == 4


def test_q_no_match_returns_empty_not_error(client_with_rows):
    client = client_with_rows(_q_rows())
    body = client.get("/bids/search?q=존재하지않는검색어").json()
    assert body["total"] == 0 and body["items"] == []


def test_q_combines_with_category(client_with_rows):
    rows = [
        make_bid(bid_id="w_00", bid_ntce_no="w", bid_ntce_nm="도로 포장", bid_category="cnstwk"),
        make_bid(bid_id="s_00", bid_ntce_no="s", bid_ntce_nm="도로 설계", bid_category="servc"),
    ]
    client = client_with_rows(rows)
    body = client.get("/bids/search?q=도로&category=cnstwk").json()
    assert body["total"] == 1 and body["items"][0]["bid_id"] == "w_00"


def _closed_mix():
    """마감된 것 2건 + 활성 2건 + 미정 1건."""
    return [
        make_bid(bid_id="old_00", bid_ntce_no="old", bid_clse_dt=datetime(2020, 1, 1)),
        make_bid(bid_id="recent_00", bid_ntce_no="recent", bid_clse_dt=datetime(2024, 1, 1)),
        make_bid(bid_id="soon_00", bid_ntce_no="soon", bid_clse_dt=datetime(2027, 1, 1)),
        make_bid(bid_id="later_00", bid_ntce_no="later", bid_clse_dt=datetime(2028, 1, 1)),
        make_bid(bid_id="null_00", bid_ntce_no="null", bid_clse_dt=None),
    ]


def test_include_closed_default_excludes_closed(client_with_rows):
    """기본은 지금과 동일 — 마감된 공고는 안 나온다."""
    body = client_with_rows(_closed_mix()).get("/bids/search").json()
    assert {i["bid_id"] for i in body["items"]} == {"soon_00", "later_00", "null_00"}


def test_include_closed_true_adds_closed_bids(client_with_rows):
    body = client_with_rows(_closed_mix()).get("/bids/search?include_closed=true").json()
    assert body["total"] == 5


def test_include_closed_keeps_active_first(client_with_rows):
    """이미 끝난 공고가 1페이지를 차지하면 안 된다 — 활성 → 마감 → 미정 순서."""
    body = client_with_rows(_closed_mix()).get("/bids/search?include_closed=true").json()
    ids = [i["bid_id"] for i in body["items"]]
    assert ids == [
        "soon_00",    # 활성, 마감 임박순
        "later_00",
        "recent_00",  # 마감, 최근에 끝난 순
        "old_00",
        "null_00",    # 마감 미정은 맨 뒤
    ]


def test_include_closed_does_not_affect_recent_sort(client_with_rows):
    """recent 정렬은 게시일 기준이라 마감 여부와 무관하게 동작한다."""
    res = client_with_rows(_closed_mix()).get("/bids/search?include_closed=true&sort=recent")
    assert res.status_code == 200 and res.json()["total"] == 5


def test_q_too_long_is_422(client_with_rows):
    client = client_with_rows(_q_rows())
    assert client.get("/bids/search?q=" + "가" * 101).status_code == 422


def test_list_endpoint_keeps_page_size_20(client_with_rows):
    """홈·추천이 쓰는 GET /bids는 응답 계약(확정본)대로 20을 유지한다."""
    client = client_with_rows([make_bid()])
    assert client.get("/bids").json()["page_size"] == 20


def test_search_paging_uses_24_per_page(client_with_rows):
    """25건이면 1페이지 24건 + 2페이지 1건. offset 계산이 24 기준인지 확인."""
    rows = [make_bid(bid_id=f"b{i:02d}_00", bid_ntce_no=f"b{i:02d}") for i in range(25)]
    client = client_with_rows(rows)
    p1 = client.get("/bids/search").json()
    p2 = client.get("/bids/search?page=2").json()
    assert p1["total"] == 25 and len(p1["items"]) == 24
    assert len(p2["items"]) == 1
    # 경계에서 행이 중복·누락되지 않아야 한다.
    ids = [i["bid_id"] for i in p1["items"]] + [i["bid_id"] for i in p2["items"]]
    assert len(set(ids)) == 25


def test_list_endpoint_still_rejects_recent(client_with_rows):
    """기존 GET /bids의 정렬 계약은 그대로 — recent는 여기서 받지 않는다."""
    client = client_with_rows([make_bid()])
    assert client.get("/bids?sort=recent").status_code == 422
    assert client.get("/bids?sort=deadline").status_code == 200
    assert client.get("/bids?sort=score").status_code == 200
