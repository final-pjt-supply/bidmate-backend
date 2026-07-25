# -*- coding: utf-8 -*-
"""GET /masters/items 계약 테스트 (라우터↔서비스↔DTO 조립).

실제 SQL(10자리 필터·is_active·ILIKE·정렬)은 실 DB로 별도 검증한다. 여기서는
진짜 MasterService에 Fake repo를 물려 응답계약·인자 전달·경계값·인증필수를 고정한다.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_master_service
from app.main import app
from app.services.master_service import MasterService


class FakeMasterRepo:
    """검색어를 그대로 기록해 서비스가 뭘 넘겼는지 확인한다."""

    def __init__(self):
        self.last: tuple[str, int] | None = None

    def search_items(self, q: str, *, limit: int):
        self.last = (q, limit)
        if not q.strip():
            return []
        return [("4321150301", "노트북컴퓨터"), ("9943211503", "융복합노트북컴퓨터")][:limit]


@pytest.fixture
def masters_client():
    repo = FakeMasterRepo()
    service = MasterService(repo)

    def _make() -> TestClient:
        app.dependency_overrides[get_master_service] = lambda: service
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id="9001", email=None
        )
        return TestClient(app)

    yield _make, repo
    app.dependency_overrides.clear()


def test_search_items_contract(masters_client):
    make, _ = masters_client
    r = make().get("/masters/items?q=노트북")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items"}
    assert body["items"][0] == {"item_code": "4321150301", "item_name": "노트북컴퓨터"}


def test_query_and_limit_passed_through(masters_client):
    make, repo = masters_client
    make().get("/masters/items?q=합판&limit=5")
    assert repo.last == ("합판", 5)


def test_default_limit_is_20(masters_client):
    make, repo = masters_client
    make().get("/masters/items?q=합판")
    assert repo.last == ("합판", 20)


def test_empty_query_returns_empty_list(masters_client):
    """검색어 없이 전체 덤프가 나가면 안 된다(35,171행)."""
    make, _ = masters_client
    r = make().get("/masters/items")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_limit_upper_bound_is_422(masters_client):
    make, _ = masters_client
    assert make().get("/masters/items?q=합판&limit=51").status_code == 422


def test_limit_lower_bound_is_422(masters_client):
    make, _ = masters_client
    assert make().get("/masters/items?q=합판&limit=0").status_code == 422


def test_masters_require_login(masters_client):
    make, _ = masters_client
    make()
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).get("/masters/items?q=합판").status_code == 401
