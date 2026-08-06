# -*- coding: utf-8 -*-
"""GET /metrics — Prometheus 텍스트 노출(운영 관측).

의존성 없이(수동 exposition) 핵심 게이지만 노출한다:
- up / build_info
- DB 커넥션 풀(ADR-31 캐스케이드 가시성)
- bid_stats matview 신선도(TS-25 — 외부 DAG가 멈추면 나이가 늘어남)
- match_results 신선도(매칭 주기 갱신이 도는지)

DB가 없어도(오프라인) 500나지 않고, 신선도는 -1로 폴백한다.
"""
from fastapi.testclient import TestClient

from app.main import app


def test_metrics_exposes_prometheus_gauges():
    res = TestClient(app).get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    body = res.text
    assert "bidmate_up 1" in body
    assert "bidmate_db_pool_size" in body
    assert "bidmate_db_pool_checked_out" in body
    assert "bidmate_bid_stats_age_seconds" in body       # matview 신선도
    assert "bidmate_match_results_age_seconds" in body    # 매칭 신선도


def test_metrics_survives_db_unavailable(monkeypatch):
    """DB 접속이 실패해도 200 + 신선도 -1 폴백(500 금지)."""
    import app.main as m

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(m.engine, "connect", boom)
    res = TestClient(app).get("/metrics")
    assert res.status_code == 200
    assert "bidmate_bid_stats_age_seconds -1" in res.text
    assert "bidmate_match_results_age_seconds -1" in res.text
