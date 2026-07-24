# -*- coding: utf-8 -*-
"""B 어댑터 — 자격판정([1] eligibility)·점수화([3a] scoring) 노드 팩토리.

ADR 0005: B 실구현은 백엔드 서비스 계층에서 팀원이 작성하되 agents.schemas
모델(EligibilityResult/MatchScore)로 반환하고, A가 이 어댑터로
build_graph(...)에 주입한다. 어댑터는 state에서 입력을 꺼내 서비스를 부르고
자기 슬롯만 채운다(agents/state.py 규칙) — 변환 이외의 로직 금지.

실구현이 붙기 전까지 run_agent()는 패키지 내장 스텁(agents/nodes/stubs.py)을
기본값으로 쓴다 — 이 팩토리는 실구현이 들어오는 즉시 주입할 이음매다.
"""
from typing import Callable, Protocol

from agents.schemas import EligibilityResult, Filters, MatchScore


class EligibilityService(Protocol):
    """B가 구현할 자격판정 서비스 계약."""

    def check(self, *, company_id: str,
              filters: Filters) -> list[EligibilityResult]: ...


class ScoringService(Protocol):
    """B가 구현할 점수화 서비스 계약."""

    def score(self, *, company_id: str,
              bid_ids: list[str]) -> list[MatchScore]: ...


def make_eligibility_node(service: EligibilityService) -> Callable[[dict], dict]:
    def eligibility_node(state: dict) -> dict:
        filters = Filters(**(state["resolved_filters"] or {}))
        return {"eligibility": service.check(
            company_id=state["company_id"], filters=filters)}
    return eligibility_node


def make_scoring_node(service: ScoringService) -> Callable[[dict], dict]:
    def scoring_node(state: dict) -> dict:
        passed = [r.bid_id for r in state["eligibility"] if r.passed]
        return {"scores": service.score(
            company_id=state["company_id"], bid_ids=passed)}
    return scoring_node
