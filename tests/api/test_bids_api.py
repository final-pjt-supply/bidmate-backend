# -*- coding: utf-8 -*-
"""GET /bids, GET /bids/{bid_id} 계약 테스트(라우터↔서비스)."""
from datetime import datetime

from tests.api.conftest import make_bid


def test_list_returns_spec_shape_and_null_match_score(client_with_rows):
    client = client_with_rows([make_bid()])
    res = client.get("/bids")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"total", "page", "page_size", "items"}
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 20
    item = body["items"][0]
    # 목록 아이템 필드 계약(qualification 미포함, match_score는 항상 null).
    assert set(item) == {
        "bid_id", "bid_ntce_nm", "dminstt_nm", "bid_category",
        "sucsfbid_mthd_nm", "bid_clse_dt", "bdgt_amt",
        "bid_prtcpt_lmt_yn", "item_tag", "match_score",
    }
    assert item["match_score"] is None
    assert item["bid_clse_dt"] == "2027-01-01T18:00:00"   # KST naive, 타임존 접미사 없음


def test_list_excludes_non_merged(client_with_rows):
    rows = [
        make_bid(bid_id="a_00", bid_ntce_no="a", qual_status="merged"),
        make_bid(bid_id="b_00", bid_ntce_no="b", qual_status="pending"),
        make_bid(bid_id="c_00", bid_ntce_no="c", qual_status="partial"),
        make_bid(bid_id="d_00", bid_ntce_no="d", qual_status="failed"),
    ]
    body = client_with_rows(rows).get("/bids").json()
    assert body["total"] == 1
    assert [i["bid_id"] for i in body["items"]] == ["a_00"]


def test_list_category_filter(client_with_rows):
    rows = [
        make_bid(bid_id="s_00", bid_ntce_no="s", bid_category="servc"),
        make_bid(bid_id="c_00", bid_ntce_no="c", bid_category="cnstwk"),
    ]
    body = client_with_rows(rows).get("/bids", params={"category": "servc"}).json()
    assert [i["bid_id"] for i in body["items"]] == ["s_00"]


def test_invalid_category_is_422(client_with_rows):
    res = client_with_rows([]).get("/bids", params={"category": "bogus"})
    assert res.status_code == 422


def test_empty_and_out_of_range_page_is_200_empty(client_with_rows):
    # 0건
    res = client_with_rows([]).get("/bids")
    assert res.status_code == 200 and res.json()["items"] == []
    # 범위 밖 page
    res = client_with_rows([make_bid()]).get("/bids", params={"page": 5})
    assert res.status_code == 200
    assert res.json() == {"total": 1, "page": 5, "page_size": 20, "items": []}


def test_deadline_sort_puts_nulls_last(client_with_rows):
    rows = [
        make_bid(bid_id="late_00", bid_ntce_no="late", bid_clse_dt=datetime(2027, 8, 1)),
        make_bid(bid_id="none_00", bid_ntce_no="none", bid_clse_dt=None),
        make_bid(bid_id="soon_00", bid_ntce_no="soon", bid_clse_dt=datetime(2027, 7, 22)),
    ]
    body = client_with_rows(rows).get("/bids").json()
    assert [i["bid_id"] for i in body["items"]] == ["soon_00", "late_00", "none_00"]


def test_score_sort_falls_back_to_deadline(client_with_rows):
    rows = [
        make_bid(bid_id="late_00", bid_ntce_no="late", bid_clse_dt=datetime(2027, 8, 1)),
        make_bid(bid_id="soon_00", bid_ntce_no="soon", bid_clse_dt=datetime(2027, 7, 22)),
    ]
    by_score = client_with_rows(rows).get("/bids", params={"sort": "score"}).json()
    assert [i["bid_id"] for i in by_score["items"]] == ["soon_00", "late_00"]


def test_list_excludes_expired_but_keeps_null_deadline(client_with_rows):
    rows = [
        make_bid(bid_id="past_00", bid_ntce_no="past", bid_clse_dt=datetime(2020, 1, 1)),
        make_bid(bid_id="future_00", bid_ntce_no="future", bid_clse_dt=datetime(2027, 1, 1)),
        make_bid(bid_id="nodl_00", bid_ntce_no="nodl", bid_clse_dt=None),
    ]
    body = client_with_rows(rows).get("/bids").json()
    ids = {i["bid_id"] for i in body["items"]}
    assert "past_00" not in ids          # 마감 지난 공고는 제외
    assert ids == {"future_00", "nodl_00"}   # 미래 + 마감없음(NULL)은 노출
    assert body["total"] == 2


def test_detail_returns_assembled_qualification(client_with_rows):
    row = make_bid(
        company_size_limit="sme_only",
        region_limit_type="hq_location",
        region_limit_names=["경기도"],
        required_licenses=[{"or_group": 1, "name_raw": "소프트웨어사업자", "code": "SW01"}],
        required_certs=["ISO 27001", "GS인증"],
        tech_weight=80,
        price_weight=20,
        award_cutline_type="score",
        award_cutline_value=85,
    )
    body = client_with_rows([row]).get("/bids/20260714123_00").json()
    assert body["match"] is None
    q = body["qualification"]
    assert q["company_size_limit"] == "sme_only"
    assert q["region_limit_names"] == ["경기도"]
    assert q["required_licenses"][0]["code"] == "SW01"
    assert q["required_certs"] == ["ISO 27001", "GS인증"]
    assert q["award_cutline_type"] == "score"
    # NUMERIC은 JSON 숫자로 나가야 한다(Decimal→문자열 방지, 프론트 명세 타입).
    assert q["tech_weight"] == 80 and isinstance(q["tech_weight"], (int, float))
    assert q["price_weight"] == 20 and isinstance(q["price_weight"], (int, float))
    assert isinstance(q["award_cutline_value"], (int, float))
    assert not isinstance(q["award_cutline_value"], str)
    # DB엔 있으나 명세 15필드 밖인 필드는 응답에 없어야 한다.
    assert "item_codes" not in q
    assert "capacity_reqs" not in q


def test_cors_allows_configured_origin(client_with_rows):
    # 프론트(브라우저) 호출이 막히지 않도록 허용 오리진이 응답 헤더에 실려야 한다.
    # 기본 허용 목록(config.cors_origins)의 로컬 개발 오리진으로 검증.
    client = client_with_rows([make_bid()])
    res = client.get("/bids", headers={"Origin": "http://localhost:5173"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_detail_missing_or_non_merged_is_404(client_with_rows):
    rows = [make_bid(bid_id="hidden_00", bid_ntce_no="hidden", qual_status="pending")]
    client = client_with_rows(rows)
    assert client.get("/bids/does-not-exist").status_code == 404
    # 비-merged도 '없음'과 동일하게 404(존재 여부 미노출).
    assert client.get("/bids/hidden_00").status_code == 404


def test_item_tag_is_served_when_present(client_with_rows):
    """품목 태그가 목록·상세에 그대로 실린다.

    실제 필터링(신뢰도 낮은 태그를 NULL로 바꾸는 것)은 Bid.item_tag의 상관
    서브쿼리가 SQL에서 하므로 여기서는 검증하지 않는다 — 이 레포의 API 테스트는
    FakeBidRepository를 쓰고 DB에 붙지 않는다. 여기서 지키는 건 응답 계약이다.
    """
    client = client_with_rows([make_bid(item_tag="IT시스템")])
    assert client.get("/bids").json()["items"][0]["item_tag"] == "IT시스템"
    assert client.get("/bids/20260714123_00").json()["item_tag"] == "IT시스템"


def test_item_tag_is_null_when_absent(client_with_rows):
    """태그가 없으면 null이다. 프론트는 이 값만 보고 배지를 그릴지 정한다 —
    '미분류' 같은 문자열을 대신 내려보내면 화면이 그걸 태그로 착각한다."""
    client = client_with_rows([make_bid()])
    assert client.get("/bids").json()["items"][0]["item_tag"] is None
    assert client.get("/bids/20260714123_00").json()["item_tag"] is None
