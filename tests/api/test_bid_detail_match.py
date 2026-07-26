# -*- coding: utf-8 -*-
"""GET /bids/{bid_id}에 매칭 판정이 붙는지 계약 테스트 (이슈 #63).

기존 test_bids_api.py의 client_with_rows는 MatchRepository를 안 주입해 match가
항상 null인 경로만 검증한다. 여기서는 매칭이 실제로 있는/없는/비로그인 세 경로를
FakeMatchRepository로 고정한다.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_bid_service, get_current_user
from app.main import app
from app.services.bid_service import BidService
from tests.api.conftest import FakeBidRepository, make_bid

COMPANY_ID = 1


class FakeMatchRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeMatchRepository:
    def __init__(self, rows: dict[tuple[int, str, str], FakeMatchRow]):
        self._rows = rows

    def get_one(self, company_id, bid_ntce_no, bid_ntce_ord):
        return self._rows.get((company_id, bid_ntce_no, bid_ntce_ord))


@pytest.fixture
def client_with_match():
    def _make(row, match_row: FakeMatchRow | None, *, logged_in: bool = True) -> TestClient:
        rows = {}
        if match_row is not None:
            rows[(COMPANY_ID, row.bid_ntce_no, row.bid_ntce_ord)] = match_row
        service = BidService(FakeBidRepository([row]), FakeMatchRepository(rows))
        app.dependency_overrides[get_bid_service] = lambda: service
        if logged_in:
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
                company_id=str(COMPANY_ID), email=None
            )
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def _match_row(**overrides):
    base = dict(
        verdict="보완가능", required=3, satisfied=2, gate_failed=0, need_review=0,
        axes=[
            {"axis": "license", "class": "gate", "status": "충족", "detail": "1/1 그룹 충족"},
            {"axis": "region", "class": "gate", "status": "충족", "detail": "본점 소재지 충족"},
            {"axis": "personnel", "class": "supp", "status": "미충족", "detail": "인력 요건 0/1"},
        ],
        computed_at=None,
    )
    base.update(overrides)
    return FakeMatchRow(**base)


def test_match_attached_when_computed(client_with_match):
    row = make_bid()
    body = client_with_match(row, _match_row()).get(f"/bids/{row.bid_id}").json()
    assert body["match"]["verdict"] == "보완가능"
    assert body["match"]["required"] == 3 and body["match"]["satisfied"] == 2
    axes = body["match"]["axes"]
    assert {a["axis"] for a in axes} == {"license", "region", "personnel"}
    assert next(a for a in axes if a["axis"] == "personnel")["status"] == "미충족"


def test_match_null_when_not_yet_computed(client_with_match):
    """프로필 미입력 등으로 match_results에 행이 없으면 null이지 에러가 아니다."""
    row = make_bid()
    body = client_with_match(row, None).get(f"/bids/{row.bid_id}").json()
    assert body["match"] is None
    assert body["qualification"] is not None   # 나머지 상세 정보는 정상 제공


def test_match_null_when_anonymous(client_with_match):
    """비로그인은 company_id가 없어 매칭 조회 자체를 안 한다."""
    row = make_bid()
    body = client_with_match(row, _match_row(), logged_in=False).get(f"/bids/{row.bid_id}").json()
    assert body["match"] is None
