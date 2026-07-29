# -*- coding: utf-8 -*-
"""인메모리 레이트 가드 단위 테스트 — 분당 슬라이딩·동시성·회사 격리(시계 주입).

일일(RDS)은 DB가 필요해 여기선 안 다룬다 — API 레벨(test_agent_chat_api)에서
대역으로, 실제 SQL은 배포 후 실 RDS 스모크로 검증한다.
"""
import pytest

from app.agents.chat_rate_limit import InMemoryChatGuard, RateLimited


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_minute_blocks_after_limit():
    g = InMemoryChatGuard(clock=FakeClock())
    for _ in range(3):
        g.check_minute(1, limit=3)          # 3회 OK
    with pytest.raises(RateLimited):
        g.check_minute(1, limit=3)          # 4회째 차단


def test_minute_window_slides_out():
    clock = FakeClock()
    g = InMemoryChatGuard(clock=clock)
    for _ in range(3):
        g.check_minute(1, limit=3)
    clock.advance(61)                        # 61초 뒤 → 옛 요청이 창 밖
    g.check_minute(1, limit=3)               # 다시 통과(예외 없음)


def test_minute_is_per_company():
    g = InMemoryChatGuard(clock=FakeClock())
    for _ in range(3):
        g.check_minute(1, limit=3)           # 회사1 소진
    g.check_minute(2, limit=3)               # 회사2는 별도 카운트 → OK


def test_retry_after_is_positive():
    g = InMemoryChatGuard(clock=FakeClock())
    g.check_minute(1, limit=1)
    with pytest.raises(RateLimited) as exc:
        g.check_minute(1, limit=1)
    assert exc.value.retry_after >= 1


def test_concurrency_cap_and_release():
    g = InMemoryChatGuard()
    g.acquire(1, limit=2)
    g.acquire(1, limit=2)
    with pytest.raises(RateLimited):
        g.acquire(1, limit=2)                # 동시 3번째 → 차단
    g.release(1)
    g.acquire(1, limit=2)                     # 하나 해제 후 다시 획득 OK


def test_reset_clears_state():
    g = InMemoryChatGuard(clock=FakeClock())
    g.check_minute(1, limit=1)
    g.acquire(1, limit=1)
    g.reset()
    g.check_minute(1, limit=1)               # 리셋되어 다시 통과
    g.acquire(1, limit=1)
