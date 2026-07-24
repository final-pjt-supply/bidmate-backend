# -*- coding: utf-8 -*-
"""C 어댑터 — 검색([2] retrieval) 노드 팩토리.

ADR 0005: C 실구현(OpenSearch bid_chunks 검색)은 팀원이 작성하되
agents.schemas.Chunk로 반환하고, A가 이 어댑터로 build_graph(...)에 주입한다.
질의는 Router가 정규화한 intent.normalized_query를 쓴다(원문 query 아님).
"""
from typing import Callable, Protocol

from agents.schemas import Chunk, Filters


class RetrievalService(Protocol):
    """C가 구현할 청크 검색 서비스 계약."""

    def search(self, *, query: str, filters: Filters) -> list[Chunk]: ...


def make_retrieval_node(service: RetrievalService) -> Callable[[dict], dict]:
    def retrieval_node(state: dict) -> dict:
        filters = Filters(**(state["resolved_filters"] or {}))
        return {"chunks": service.search(
            query=state["intent"].normalized_query, filters=filters)}
    return retrieval_node
