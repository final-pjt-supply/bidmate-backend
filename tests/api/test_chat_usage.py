# -*- coding: utf-8 -*-
"""챗 일일 카운터의 시각 로직 단위 테스트 — KST 경계·Retry-After (QA M4).

today_kst/seconds_to_kst_midnight는 시계를 주입받지 않으므로 모듈의 datetime을
고정 시각으로 monkeypatch해 KST 경계 동작을 결정적으로 고정한다. 원자 UPSERT
(_INCR_SQL)는 DB가 필요해 여기선 안 다루고 실 RDS 스모크로 검증한다.
"""
from datetime import date, datetime, timezone

import pytest

from app.infra.db.repositories import chat_usage_repository as mod

_UTC = timezone.utc


def _freeze(monkeypatch, fixed_utc: datetime):
    """모듈의 datetime.now(tz)가 항상 fixed_utc(그 tz로 변환)를 돌려주게 고정."""
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc
    monkeypatch.setattr(mod, "datetime", _Frozen)


def test_today_kst_uses_kst_not_utc(monkeypatch):
    """UTC로는 아직 07-30이지만 KST로는 07-31인 순간 → today_kst는 07-31."""
    # 2026-07-30 15:30 UTC == 2026-07-31 00:30 KST
    _freeze(monkeypatch, datetime(2026, 7, 30, 15, 30, tzinfo=_UTC))
    assert mod.today_kst() == date(2026, 7, 31)          # KST 날짜
    assert mod.today_kst() != date(2026, 7, 30)          # UTC 날짜가 아니어야


def test_today_kst_same_day_within_kst(monkeypatch):
    """KST 정오면 당일."""
    _freeze(monkeypatch, datetime(2026, 7, 30, 3, 0, tzinfo=_UTC))  # 12:00 KST
    assert mod.today_kst() == date(2026, 7, 30)


def test_seconds_to_kst_midnight_positive_and_bounded(monkeypatch):
    """KST 00:30이면 다음 자정까지 ~23.5h — 양수이고 하루(86400) 이내."""
    _freeze(monkeypatch, datetime(2026, 7, 30, 15, 30, tzinfo=_UTC))  # 00:30 KST
    secs = mod.seconds_to_kst_midnight()
    assert 0 < secs <= 86400
    assert secs == pytest.approx((23 * 3600 + 30 * 60), abs=5)


def test_seconds_to_kst_midnight_never_zero(monkeypatch):
    """자정 직전(23:59:59.5 KST)에도 최소 1초를 보장한다(Retry-After=0 방지)."""
    _freeze(monkeypatch, datetime(2026, 7, 30, 14, 59, 59, 500000, tzinfo=_UTC))  # 23:59:59.5 KST
    assert mod.seconds_to_kst_midnight() >= 1
