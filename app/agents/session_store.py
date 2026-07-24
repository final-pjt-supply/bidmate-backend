# -*- coding: utf-8 -*-
"""에이전트 대화 세션 스토어(인메모리).

ADR 0005 세션 왕복 규약: AgentResponse.session_context를 세션별로 저장했다가
다음 턴 AgentRequest에 '수정 없이 그대로' 넣는다. 인메모리 보관은 EC2 상시
프로세스 전제 — Lambda(Mangum) 경로로 전환하면 이벤트 sink와 함께 외부
스토어(Redis 등)로 옮긴다(같은 제약을 이미 공유).
"""
import uuid
from collections import OrderedDict
from functools import lru_cache
from threading import Lock

from agents.schemas import SessionContext

_MAX_SESSIONS = 1000   # 무한 증식 방지 — 넘치면 가장 오래 안 쓰인 세션부터 버린다.


class InMemorySessionStore:
    """세션ID → SessionContext 보관함. uvicorn이 sync 라우트를 스레드풀에서
    돌리므로 Lock으로 보호한다. 상한 초과 시 LRU 순서로 버린다."""

    def __init__(self, max_sessions: int = _MAX_SESSIONS) -> None:
        self._max = max_sessions
        self._store: OrderedDict[str, SessionContext] = OrderedDict()
        self._lock = Lock()

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    def get(self, session_id: str) -> SessionContext | None:
        with self._lock:
            ctx = self._store.get(session_id)
            if ctx is not None:
                self._store.move_to_end(session_id)
            return ctx

    def set(self, session_id: str, ctx: SessionContext) -> None:
        with self._lock:
            self._store[session_id] = ctx
            self._store.move_to_end(session_id)
            while len(self._store) > self._max:
                self._store.popitem(last=False)


@lru_cache(maxsize=1)
def get_session_store() -> InMemorySessionStore:
    """앱 수명 동안 하나의 스토어(이벤트 sink의 get_event_sink 패턴과 동일)."""
    return InMemorySessionStore()
