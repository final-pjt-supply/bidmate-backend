# -*- coding: utf-8 -*-
"""요청 관측성 — 모든 응답에 요청 ID, 로그에 경로·상태·소요시간.

백엔드에 APM이 없어 p95·병목을 아무도 모른다. 최소한의 요청 로깅으로 이후
성능 판단(캐시·비동기화 등)을 숫자로 할 수 있게 한다.
"""
import logging

from fastapi.testclient import TestClient

from app.main import app


def test_response_has_request_id_header():
    """모든 응답에 상관관계용 요청 ID가 실린다(로그↔응답 연결)."""
    res = TestClient(app).get("/health")
    assert res.status_code == 200
    assert res.headers.get("x-request-id")   # 비어있지 않은 ID


def test_request_is_logged_with_path_status_and_duration(caplog):
    """요청 로그에 경로·상태코드·소요시간(ms)이 남는다."""
    with caplog.at_level(logging.INFO, logger="app.request"):
        TestClient(app).get("/health")
    recs = [r for r in caplog.records if r.name == "app.request"]
    assert recs, "app.request 로그가 남아야 한다"
    msg = recs[-1].getMessage()
    assert "/health" in msg
    assert "200" in msg
    assert "ms" in msg
