# -*- coding: utf-8 -*-
"""매칭 주기 갱신(#80) — 모드 판정과 실패 격리를 DB 없이 고정한다.

실제 SQL(compute_match_results 호출·ON CONFLICT UPSERT)은 운영 RDS 데이터라
여기서 돌리지 않는다(쓰기 테스트 금지 — test_bid_repository_integration.py의
_PROD_DB_NAMES 가드와 같은 원칙). 여기서 고정하는 건 '언제 무엇을 부르는가'다:

  - 새 정규화가 없으면 DB 쓰기가 0이어야 한다(5분마다 헛돌지 않게)
  - 최초 기동(계산 이력 없음)은 증분이 아니라 전체로 채워야 한다
  - 증분 하한은 계산 시각보다 앞이어야 한다(경계 공고 유실 방지)
  - 루프는 어떤 예외에도 죽지 않아야 한다(죽으면 조용히 갱신이 멈춘다)
"""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.services.match_refresh import MatchRefreshService, match_refresh_loop

_NOW = datetime(2026, 7, 28, 22, 30)


class FakeMatchRepo:
    """MatchRepository의 주기 갱신 부분만 흉내낸다. 호출 이력을 남긴다."""

    def __init__(self, *, companies=(1, 4), normalized_at=None, computed_at=None):
        self._companies = list(companies)
        self._normalized_at = normalized_at
        self._computed_at = computed_at
        self.upserts: list[tuple[int, datetime]] = []
        self.recomputes: list[int] = []

    def active_company_ids(self):
        return list(self._companies)

    def latest_normalized_at(self):
        return self._normalized_at

    def latest_computed_at(self):
        return self._computed_at

    def upsert_since(self, company_id, since):
        self.upserts.append((company_id, since))
        return 7

    def recompute_for_company(self, company_id):
        self.recomputes.append(company_id)
        return 1400


def test_no_new_normalization_writes_nothing():
    """신규 정규화가 없으면 쓰기 0 — 5분마다 도는 배치의 기본 경로."""
    repo = FakeMatchRepo(normalized_at=_NOW - timedelta(hours=1), computed_at=_NOW)
    summary = MatchRefreshService(repo).run_once()
    assert repo.upserts == [] and repo.recomputes == []
    assert summary["rows"] == 0 and summary["skipped"] == "신규 정규화 없음"


def test_new_normalization_upserts_each_company():
    repo = FakeMatchRepo(companies=(1, 4, 11),
                         normalized_at=_NOW, computed_at=_NOW - timedelta(minutes=10))
    summary = MatchRefreshService(repo).run_once()
    assert [cid for cid, _ in repo.upserts] == [1, 4, 11]
    assert repo.recomputes == []                     # 증분은 전체 교체를 부르지 않는다
    assert summary["mode"] == "incremental" and summary["rows"] == 21


def test_incremental_since_precedes_computed_at():
    """하한을 계산 시각 그대로 쓰면 같은 순간에 커밋된 공고를 놓친다."""
    computed = _NOW - timedelta(minutes=10)
    repo = FakeMatchRepo(companies=(1,), normalized_at=_NOW, computed_at=computed)
    MatchRefreshService(repo).run_once()
    (_, since), = repo.upserts
    assert since < computed


def test_first_run_without_history_does_full():
    """계산 이력이 없으면 증분의 기준점이 없다 — 전체로 채운다."""
    repo = FakeMatchRepo(normalized_at=_NOW, computed_at=None)
    summary = MatchRefreshService(repo).run_once()
    assert repo.recomputes == [1, 4] and repo.upserts == []
    assert summary["mode"] == "full(최초)"


def test_full_mode_replaces_every_company():
    repo = FakeMatchRepo(normalized_at=_NOW, computed_at=_NOW)
    summary = MatchRefreshService(repo).run_once(full=True)
    assert repo.recomputes == [1, 4]                 # 신선도와 무관하게 전부 다시 만든다
    assert summary["mode"] == "full" and summary["rows"] == 2800


def test_no_companies_is_noop():
    repo = FakeMatchRepo(companies=(), normalized_at=_NOW, computed_at=None)
    summary = MatchRefreshService(repo).run_once()
    assert repo.recomputes == [] and summary["rows"] == 0


def test_missing_normalization_data_is_noop():
    """정규화 테이블이 비어 있으면(초기 구축) 계산할 근거가 없다."""
    repo = FakeMatchRepo(normalized_at=None, computed_at=None)
    summary = MatchRefreshService(repo).run_once()
    assert repo.recomputes == [] and repo.upserts == []
    assert summary["skipped"] == "정규화 데이터 없음"


# ── 루프의 실패 격리 ─────────────────────────────────────────
# pytest-asyncio를 쓰지 않는다 — 배포 이미지가 쓰는 app/requirements.txt에
# 테스트 전용 의존성을 늘리지 않으려고 asyncio.run으로 직접 돌린다.
def _run(coro_factory, *, timeout: float = 5.0):
    """루프를 띄우고 조건이 찰 때까지 돌린 뒤 취소한다. 멈추면 타임아웃으로 실패."""

    async def main():
        await asyncio.wait_for(coro_factory(), timeout)

    asyncio.run(main())


def test_loop_survives_db_failure():
    """세션 생성이 터져도 루프는 살아 다음 주기를 돈다."""
    calls = []

    def exploding_factory():
        calls.append(1)
        raise RuntimeError("DB 접속 실패")

    async def scenario():
        task = asyncio.create_task(
            match_refresh_loop(exploding_factory, interval_sec=0, full_hour=4)
        )
        while len(calls) < 3:            # 루프가 죽었다면 여기서 진행이 멈춘다
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario)


def test_loop_runs_full_once_per_day_at_configured_hour():
    """전체 재계산은 지정 시각에 하루 한 번 — 그 시간대 매 주기마다 돌면 안 된다."""
    repo = FakeMatchRepo(normalized_at=_NOW, computed_at=_NOW)
    four_am = datetime(2026, 7, 29, 4, 0)

    async def scenario():
        task = asyncio.create_task(
            match_refresh_loop(
                lambda: _SessionStub(repo), interval_sec=0, full_hour=4,
                now_fn=lambda: four_am,
            )
        )
        while len(repo.recomputes) < 2:              # 회사 2곳 = 전체 1회분
            await asyncio.sleep(0)
        await asyncio.sleep(0.05)                    # 같은 시각으로 여러 주기 더 돈다
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(scenario)
    assert repo.recomputes == [1, 4]                 # 두 번째 전체 재계산이 없어야 한다


class _SessionStub:
    """session_factory()가 돌려주는 세션 대역 — repository는 주입된 Fake를 쓴다."""

    def __init__(self, repo):
        self.repo = repo
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_repository(monkeypatch):
    """루프가 세션으로 만드는 MatchRepository를 세션에 실린 Fake로 바꾼다."""
    import app.services.match_refresh as mod

    monkeypatch.setattr(
        mod, "MatchRepository",
        lambda session: session.repo if isinstance(session, _SessionStub) else session,
    )
