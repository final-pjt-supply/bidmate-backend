# -*- coding: utf-8 -*-
"""GET /me/matches 계약 테스트 (라우터↔서비스↔DTO 조립).

실제 SQL(match_results ⋈ bid_table, merged 게이트·마감 필터·정렬)은 실 DB로 별도
검증한다. 여기서는 진짜 MatchService에 Fake repo를 물려 응답계약·중첩구조·정렬 전달·
인증필수·회사격리·페이지 계약을 in-memory로 고정한다.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_match_service
from app.main import app
from app.services.match_service import MatchService

COMPANY_WITH_DATA = 9001


def _bid(bid_id: str):
    return SimpleNamespace(
        bid_id=bid_id, bid_ntce_nm=f"{bid_id} 공고", dminstt_nm="어느기관",
        bid_category="servc", sucsfbid_mthd_nm=None,
        bid_clse_dt=datetime(2026, 8, 1, 10, 0), bdgt_amt=1_000_000,
        bid_prtcpt_lmt_yn=None, match_score=None,
    )


def _match(verdict: str):
    return SimpleNamespace(
        verdict=verdict, required=3, satisfied=2, gate_failed=1, need_review=0,
        axes=[{"axis": "direct_prod", "class": "gate", "detail": "직생 0/1", "status": "미충족"}],
        computed_at=datetime(2026, 7, 24, 14, 43),
    )


def _rows_for(company_id: int):
    if company_id != COMPANY_WITH_DATA:
        return []
    return [(_match("가능"), _bid("bid_a")), (_match("불가"), _bid("bid_b"))]


class FakeMatchRepo:
    def __init__(self):
        self.last_sort: str | None = None
        self.last_include: bool | None = None

    def _visible(self, company_id: int, include_infeasible: bool):
        rows = _rows_for(company_id)
        if include_infeasible:
            return rows
        return [r for r in rows if r[0].verdict != "불가"]

    def count(self, company_id: int, *, clse_after, include_infeasible=False) -> int:
        self.last_include = include_infeasible
        return len(self._visible(company_id, include_infeasible))

    def list_page(self, company_id: int, *, sort, limit, offset, clse_after,
                  include_infeasible=False):
        self.last_sort = sort            # 라우터가 넘긴 정렬키를 기록해 검증
        self.last_include = include_infeasible
        rows = self._visible(company_id, include_infeasible)
        return rows[offset:offset + limit]


@pytest.fixture
def match_client():
    repo = FakeMatchRepo()
    service = MatchService(repo)

    def _make(company_id: str = str(COMPANY_WITH_DATA)) -> TestClient:
        app.dependency_overrides[get_match_service] = lambda: service
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=company_id, email=None
        )
        return TestClient(app)

    yield _make, repo
    app.dependency_overrides.clear()


def test_matches_full_contract(match_client):
    make, _ = match_client
    c = make()
    r = c.get("/me/matches")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"total", "page", "page_size", "items"}
    assert body["total"] == 1 and body["page_size"] == 24   # '불가'는 기본 제외
    first = body["items"][0]
    # 중첩 구조: bid + match
    assert set(first) == {"bid", "match"}
    assert first["bid"]["bid_id"] == "bid_a"
    assert first["match"]["verdict"] == "가능"
    assert first["match"]["axes"][0]["axis"] == "direct_prod"


def test_default_sort_is_deadline(match_client):
    make, repo = match_client
    make().get("/me/matches")
    assert repo.last_sort == "deadline"   # 기본 정렬


def test_recent_sort_passed_through(match_client):
    make, repo = match_client
    make().get("/me/matches?sort=recent")
    assert repo.last_sort == "recent"


def test_invalid_sort_is_422(match_client):
    make, _ = match_client
    assert make().get("/me/matches?sort=bogus").status_code == 422


def test_empty_for_company_without_matches(match_client):
    make, _ = match_client
    body = make(company_id="12345").get("/me/matches").json()
    assert body["total"] == 0 and body["items"] == []


def test_out_of_range_page_is_empty(match_client):
    make, _ = match_client
    body = make().get("/me/matches?page=99").json()
    assert body["total"] == 1 and body["items"] == []


def test_matches_require_login(match_client):
    make, _ = match_client
    make()
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).get("/me/matches").status_code == 401


# ── 참가 불가 제외 (이슈 #59) ─────────────────────────────────────────
def test_infeasible_excluded_by_default(match_client):
    """실측상 판정의 65%가 '불가' — 기본으로 빼지 않으면 추천 목록이 불가로 채워진다."""
    make, repo = match_client
    body = make().get("/me/matches").json()
    assert repo.last_include is False
    assert [i["match"]["verdict"] for i in body["items"]] == ["가능"]


def test_infeasible_included_when_asked(match_client):
    make, repo = match_client
    body = make().get("/me/matches?include_infeasible=true").json()
    assert repo.last_include is True
    assert body["total"] == 2
    assert {i["match"]["verdict"] for i in body["items"]} == {"가능", "불가"}


def test_total_matches_filtered_list(match_client):
    """total과 목록이 같은 필터를 써야 페이지네이션이 어긋나지 않는다."""
    make, _ = match_client
    body = make().get("/me/matches").json()
    assert body["total"] == len(body["items"])


# ── 홈 대시보드 요약 ─────────────────────────────────────────────────
def test_summary_contract(match_client):
    make, _ = match_client
    r = make().get("/me/matches/summary")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"total"}
    assert body["total"] == 1   # '불가' 제외


def test_summary_matches_list_total(match_client):
    """요약과 목록이 같은 필터를 써야 홈 건수와 추천 화면이 같은 말을 한다."""
    make, _ = match_client
    c = make()
    assert c.get("/me/matches/summary").json()["total"] == c.get("/me/matches").json()["total"]


def test_summary_requires_login(match_client):
    make, _ = match_client
    make()
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).get("/me/matches/summary").status_code == 401
