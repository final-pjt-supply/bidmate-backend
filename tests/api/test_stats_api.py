# -*- coding: utf-8 -*-
"""GET /stats 계약 테스트 (라우터↔서비스↔DTO 조립).

집계 SQL 자체는 matview 정의(alembic 0006)의 몫이라 여기서 검증하지 않는다.
그쪽은 파이썬 원본(gen-stats-mock.py)과 45개 조건 × 8항목 대조로 확인했다.
여기서는 진짜 StatsService에 Fake repo를 물려 폴백 규칙·응답계약·에러 매핑을 고정한다.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_stats_service
from app.main import app
from app.services.stats_service import StatsService

COMPUTED_AT = datetime(2026, 8, 3, 4, 0, 0)


def payload(total: int, *, amount_available: bool = True) -> dict:
    return {
        "period": {"from": "2026-01", "to": "2026-07"},
        "total": total,
        "amount_available": amount_available,
        "tags": [{"tag": "IT시스템", "cnt": 3685}],
        "monthly": [{"m": "2026-01", "cnt": 2326}],
        "budget": {"buckets": [{"b": 2, "cnt": 10960}], "total": 23661},
        "institutions": [{"name": "육군군수사령부", "cnt": 492}],
        "excluded_mas": 1271,
    }


class FakeStatsRepo:
    """(category, tag) → payload. 요청 인자를 기록해 폴백 경로를 확인한다."""

    def __init__(self, rows: dict[tuple[str, str], dict] | None = None):
        self.rows = rows if rows is not None else {
            ("ALL", "ALL"): payload(24226),
            ("thng", "ALL"): payload(9469),
            ("thng", "IT·통신장비"): payload(1246),
            ("frgcpt", "ALL"): payload(565, amount_available=False),
        }
        self.calls: list[tuple[str, str]] = []

    def get(self, category: str, tag: str):
        self.calls.append((category, tag))
        row = self.rows.get((category, tag))
        return (row, COMPUTED_AT) if row else None


@pytest.fixture
def stats_client():
    repo = FakeStatsRepo()

    def _make(r=None) -> TestClient:
        app.dependency_overrides[get_stats_service] = lambda: StatsService(r or repo)
        return TestClient(app)

    yield _make, repo
    app.dependency_overrides.clear()


def test_default_is_all_category(stats_client):
    make, repo = stats_client
    r = make().get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["conditions"] == {"category": "ALL", "tag": None}
    assert body["total"] == 24226
    assert repo.calls == [("ALL", "ALL")]


def test_response_contract(stats_client):
    make, _ = stats_client
    body = make().get("/stats").json()
    assert set(body) == {
        "conditions", "period", "computed_at", "total", "amount_available",
        "tags", "monthly", "budget", "institutions", "excluded_mas",
    }
    # 'from'은 파이썬 예약어라 alias로 나간다 — 키 이름이 바뀌면 프론트가 깨진다.
    assert body["period"] == {"from": "2026-01", "to": "2026-07"}
    assert body["budget"] == {"buckets": [{"b": 2, "cnt": 10960}], "total": 23661}
    assert body["institutions"] == [{"name": "육군군수사령부", "cnt": 492}]


def test_category_and_tag_passed_through(stats_client):
    make, repo = stats_client
    body = make().get("/stats?category=thng&tag=IT·통신장비").json()
    assert body["conditions"] == {"category": "thng", "tag": "IT·통신장비"}
    assert body["total"] == 1246
    assert repo.calls == [("thng", "IT·통신장비")]


def test_unknown_tag_falls_back_to_all(stats_client):
    """상위 8개 밖의 태그는 404가 아니라 전체로 폴백한다.

    고를 수 있는 품목 목록이 이 응답 안에 있어서 클라이언트가 미리 검증할 수 없다.
    404로 돌리면 URL 하나 잘못 건드렸을 때 화면이 통째로 에러 페이지가 된다.
    """
    make, repo = stats_client
    r = make().get("/stats?category=thng&tag=없는태그")
    assert r.status_code == 200
    body = r.json()
    assert body["conditions"] == {"category": "thng", "tag": None}
    assert body["total"] == 9469
    assert repo.calls == [("thng", "없는태그"), ("thng", "ALL")]


def test_blank_tag_is_treated_as_all(stats_client):
    make, repo = stats_client
    assert make().get("/stats?category=thng&tag=%20").status_code == 200
    assert repo.calls == [("thng", "ALL")]


def test_invalid_category_is_422(stats_client):
    """category는 폴백하지 않는다 — 클라이언트가 요청 전에 알 수 있는 값이다."""
    make, _ = stats_client
    assert make().get("/stats?category=xyz").status_code == 422


def test_amount_unavailable_category(stats_client):
    """외자는 금액 정보가 없다. 프론트가 예산 분포를 안 그리도록 플래그를 내린다."""
    make, _ = stats_client
    body = make().get("/stats?category=frgcpt").json()
    assert body["amount_available"] is False


def test_empty_matview_is_503_not_empty_200(stats_client):
    """아직 계산되지 않은 것과 데이터가 0건인 것은 다르다.

    빈 값을 200으로 주면 화면이 "공고 0건"이라는 거짓을 그린다.
    """
    make, _ = stats_client
    r = make(FakeStatsRepo(rows={})).get("/stats")
    assert r.status_code == 503


def test_missing_matview_is_503_not_500():
    """matview 자체가 없을 때(마이그레이션 미적용) 500이 아니라 503이어야 한다.

    CD가 RUN_MIGRATIONS=false를 기본으로 두므로 코드가 먼저 배포되고 마이그레이션이
    나중에 켜지는 구간이 반드시 생긴다. 실제로 확인된 경로다 — 이 처리가 없으면
    그 구간 내내 500이 난다.
    """
    from sqlalchemy.exc import ProgrammingError

    from app.infra.db.repositories.stats_repository import StatsRepository

    class UndefinedTableSession:
        def __init__(self):
            self.rolled_back = False

        def execute(self, *_args, **_kwargs):
            raise ProgrammingError("SELECT", {}, Exception('relation "bid_stats" does not exist'))

        def rollback(self):
            # 롤백하지 않으면 같은 세션의 후속 쿼리가 전부 InFailedSqlTransaction으로 죽는다.
            self.rolled_back = True

    session = UndefinedTableSession()
    repo = StatsRepository(session)
    assert repo.get("ALL", "ALL") is None
    assert session.rolled_back is True

    app.dependency_overrides[get_stats_service] = lambda: StatsService(repo)
    try:
        assert TestClient(app).get("/stats").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_stats_require_no_login(stats_client):
    """집계값이라 회사별로 달라지지 않는다. 비로그인 조회 가능."""
    make, _ = stats_client
    make()
    assert TestClient(app).get("/stats").status_code == 200
