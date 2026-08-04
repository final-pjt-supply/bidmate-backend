# -*- coding: utf-8 -*-
"""챗 인메모리 레이트 가드 — 회사별 분당 슬라이딩 창 + 동시 실행 캡.

단일 상시 프로세스 전제(현재 컨테이너 1대). 다중 인스턴스/Lambda 전환 시에는 각
프로세스가 독립 카운트해 한도가 느슨해지므로 Redis 등 공용 저장소로 이관한다.
'일일' 상한은 재배포에 리셋되면 안 돼 RDS(chat_usage_repository)에 따로 둔다.

시계는 주입 가능(테스트가 60초 창을 결정론적으로 검증). 기본은 monotonic —
시스템 시각이 뒤로 점프해도 창이 깨지지 않는다.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimited(Exception):
    """레이트 초과. retry_after(초)를 담아 라우터가 429 + Retry-After로 변환."""

    def __init__(self, retry_after: int):
        super().__init__("rate limited")
        self.retry_after = max(1, int(retry_after))


_WINDOW = 60.0   # 분당 = 최근 60초


class InMemoryChatGuard:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[int, deque] = defaultdict(deque)   # 회사 → 최근 요청 시각
        self._active: dict[int, int] = defaultdict(int)     # 회사 → 진행 중 요청 수
        self._lock = threading.Lock()

    def check_minute(self, company_id: int, limit: int) -> None:
        """분당 슬라이딩 창. 초과면 RateLimited(가장 오래된 요청이 빠질 때까지)."""
        now = self._clock()
        with self._lock:
            hits = self._hits[company_id]
            while hits and hits[0] <= now - _WINDOW:
                hits.popleft()
            if len(hits) >= limit:
                raise RateLimited(hits[0] + _WINDOW - now)
            hits.append(now)

    def acquire(self, company_id: int, limit: int) -> None:
        """동시 실행 슬롯 획득. 초과면 RateLimited(곧 재시도)."""
        with self._lock:
            if self._active[company_id] >= limit:
                raise RateLimited(1)
            self._active[company_id] += 1

    def release(self, company_id: int) -> None:
        with self._lock:
            n = self._active.get(company_id, 0)
            if n <= 1:
                self._active.pop(company_id, None)   # 빈 항목은 정리(메모리)
            else:
                self._active[company_id] = n - 1

    def reset(self) -> None:
        """테스트 전용 — 프로세스 전역 상태 초기화."""
        with self._lock:
            self._hits.clear()
            self._active.clear()


# 프로세스 전역 싱글턴(세션 가드 _inflight와 동일 패턴).
guard = InMemoryChatGuard()
