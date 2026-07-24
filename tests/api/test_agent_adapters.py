# -*- coding: utf-8 -*-
"""B·C 어댑터 계약 테스트 — state에서 입력을 꺼내 서비스를 부르고 자기 슬롯만
채우는지 고정한다. 실구현(팀원 B·C 작성)은 이 시그니처에 맞춰 들어온다."""
from agents.schemas import (Chunk, EligibilityResult, Filters, MatchScore,
                            QueryIntent)

from app.agents.adapters import (make_eligibility_node, make_retrieval_node,
                                 make_scoring_node)


def make_intent(**overrides) -> QueryIntent:
    base = dict(type="full", action="answer", scope="new",
                entry_bid_scope="keep", new_filters=Filters(),
                normalized_query="대전 전기공사 공고")
    base.update(overrides)
    return QueryIntent(**base)


class FakeEligibilityService:
    def __init__(self):
        self.calls = []
        self.result = [EligibilityResult(bid_id="B1", passed=True)]

    def check(self, *, company_id, filters):
        self.calls.append({"company_id": company_id, "filters": filters})
        return self.result


class FakeRetrievalService:
    def __init__(self):
        self.calls = []
        self.result = [Chunk(bid_id="B1", document_id="d1", file_id="f1",
                             chunk_idx=0, text="본문", type="text")]

    def search(self, *, query, filters):
        self.calls.append({"query": query, "filters": filters})
        return self.result


class FakeScoringService:
    def __init__(self):
        self.calls = []
        self.result = [MatchScore(bid_id="B1", total=72.0, breakdown=[])]

    def score(self, *, company_id, bid_ids):
        self.calls.append({"company_id": company_id, "bid_ids": bid_ids})
        return self.result


def test_eligibility_node_calls_service_and_fills_own_slot():
    svc = FakeEligibilityService()
    node = make_eligibility_node(svc)
    out = node({"company_id": "C1",
                "resolved_filters": {"region": "대전"}})
    assert out == {"eligibility": svc.result}          # 자기 슬롯만
    assert svc.calls == [{"company_id": "C1",
                          "filters": Filters(region="대전")}]


def test_eligibility_node_handles_none_filters():
    svc = FakeEligibilityService()
    out = make_eligibility_node(svc)({"company_id": "C1",
                                      "resolved_filters": None})
    assert out == {"eligibility": svc.result}
    assert svc.calls[0]["filters"] == Filters()


def test_retrieval_node_uses_normalized_query():
    svc = FakeRetrievalService()
    node = make_retrieval_node(svc)
    out = node({"company_id": "C1", "resolved_filters": {"region": "대전"},
                "intent": make_intent()})
    assert out == {"chunks": svc.result}
    assert svc.calls == [{"query": "대전 전기공사 공고",
                          "filters": Filters(region="대전")}]


def test_scoring_node_passes_only_passed_bid_ids():
    svc = FakeScoringService()
    node = make_scoring_node(svc)
    out = node({"company_id": "C1", "eligibility": [
        EligibilityResult(bid_id="B1", passed=True),
        EligibilityResult(bid_id="B2", passed=False),
        EligibilityResult(bid_id="B3", passed=True),
    ]})
    assert out == {"scores": svc.result}
    assert svc.calls == [{"company_id": "C1", "bid_ids": ["B1", "B3"]}]
