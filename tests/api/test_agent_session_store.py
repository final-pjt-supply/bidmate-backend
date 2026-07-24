# -*- coding: utf-8 -*-
"""세션 스토어 단위 테스트 — 왕복 보관과 상한(LRU) 동작을 고정한다."""
from agents.schemas import Filters, SessionContext

from app.agents.session_store import InMemorySessionStore


def make_ctx(summary: str) -> SessionContext:
    return SessionContext(last_bid_ids=["B1"], last_summary=summary,
                          last_filters=Filters())


def test_set_then_get_returns_same_context():
    store = InMemorySessionStore()
    sid = store.new_session_id()
    ctx = make_ctx("턴1")
    store.set(sid, ctx)
    assert store.get(sid) is ctx          # 수정 없이 그대로 — 복사도 하지 않는다


def test_get_unknown_session_returns_none():
    store = InMemorySessionStore()
    assert store.get("없는-세션") is None


def test_new_session_ids_are_unique():
    store = InMemorySessionStore()
    assert store.new_session_id() != store.new_session_id()


def test_evicts_oldest_beyond_max_sessions():
    store = InMemorySessionStore(max_sessions=2)
    store.set("a", make_ctx("a"))
    store.set("b", make_ctx("b"))
    store.set("c", make_ctx("c"))
    assert store.get("a") is None
    assert store.get("b") is not None and store.get("c") is not None


def test_get_refreshes_recency():
    store = InMemorySessionStore(max_sessions=2)
    store.set("a", make_ctx("a"))
    store.set("b", make_ctx("b"))
    store.get("a")                         # a를 최신으로
    store.set("c", make_ctx("c"))          # b가 밀려난다
    assert store.get("b") is None
    assert store.get("a") is not None
