# -*- coding: utf-8 -*-
"""추천 축별 점수 계산 — 가중치 튜닝 시 회귀를 잡는 기준선."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.recommendation_scoring import (
    InterestSignal,
    extract_license_codes,
    score_bid,
)

NOW = datetime(2026, 7, 29, 12, 0, 0)


def _bid(**kw):
    base = dict(bid_clse_dt=NOW + timedelta(days=7), bdgt_amt=1_000_000_000,
                presmpt_prce=1_000_000_000, required_licenses=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _match(verdict="가능", required=9, satisfied=9):
    return SimpleNamespace(verdict=verdict, required=required, satisfied=satisfied)


def _axis(result, key):
    return next(a for a in result.axes if a.key == key)


# ── 면허 코드 추출 ─────────────────────────────────────────────────
def test_extract_uses_code_field_first():
    assert extract_license_codes([{"code": "0037", "name_raw": "전기공사업"}]) == {"0037"}


def test_extract_falls_back_to_name_raw():
    """code가 비어도 name_raw 괄호에서 뽑는다 — 실데이터 21,776건 중 code는 7,663건뿐."""
    got = extract_license_codes([
        {"code": None, "name_raw": "소프트웨어사업자(컴퓨터관련서비스사업)(1468)"},
        {"code": None, "name_raw": "방송영상독립제작자(3230) 업종"},
    ])
    assert got == {"1468", "3230"}


def test_extract_ignores_unparseable():
    """괄호 코드가 없는 자연어 요건은 버린다(잘못 뽑느니 모르는 편이 낫다)."""
    assert extract_license_codes([{"code": None, "name_raw": "사업수행 경험 및 전문성"}]) == set()


# ── 자격 축 ────────────────────────────────────────────────────────
def test_verdict_ranking():
    """가능 > 보완가능 > 확인필요 순서가 유지된다."""
    def elig(verdict):
        r = score_bid(bid=_bid(), match=_match(verdict, required=0, satisfied=0),
                      interest=None, company_license_codes=set(),
                      company_perf_amounts=[], now=NOW)
        return _axis(r, "eligibility").score

    assert elig("가능") > elig("보완가능") > elig("확인필요")
    assert all(0 <= elig(v) <= 1 for v in ("가능", "보완가능", "확인필요"))


def test_satisfaction_rate_breaks_verdict_tie():
    """같은 '확인필요'라도 축을 많이 채운 쪽이 높다."""
    high = score_bid(bid=_bid(), match=_match("확인필요", 9, 8), interest=None,
                     company_license_codes=set(), company_perf_amounts=[], now=NOW)
    low = score_bid(bid=_bid(), match=_match("확인필요", 9, 1), interest=None,
                    company_license_codes=set(), company_perf_amounts=[], now=NOW)
    assert _axis(high, "eligibility").score > _axis(low, "eligibility").score


# ── 역량 축 ────────────────────────────────────────────────────────
def test_license_hit_scores_full():
    r = score_bid(bid=_bid(required_licenses=[{"code": "0037", "name_raw": "전기공사업"}]),
                  match=_match(), interest=None, company_license_codes={"0037"},
                  company_perf_amounts=[], now=NOW)
    assert _axis(r, "capability").score == 1.0


def test_license_miss_scores_zero():
    r = score_bid(bid=_bid(required_licenses=[{"code": "0037", "name_raw": "전기공사업"}]),
                  match=_match(), interest=None, company_license_codes={"9999"},
                  company_perf_amounts=[], now=NOW)
    assert _axis(r, "capability").score == 0.0


def test_unknown_requirement_is_neutral_not_zero():
    """요구 면허를 모르는 공고에 0점을 주면 '요건이 적힌 공고'만 불리해진다."""
    r = score_bid(bid=_bid(required_licenses=None), match=_match(), interest=None,
                  company_license_codes={"0037"}, company_perf_amounts=[], now=NOW)
    assert _axis(r, "capability").score == 0.5


# ── 규모 축 ────────────────────────────────────────────────────────
def test_similar_scale_scores_full():
    r = score_bid(bid=_bid(bdgt_amt=1_000_000_000), match=_match(), interest=None,
                  company_license_codes=set(), company_perf_amounts=[1_000_000_000], now=NOW)
    assert _axis(r, "scale").score == 1.0


def test_oversized_bid_is_penalized():
    """실적의 20배짜리 공고는 수행 부담이 크다."""
    r = score_bid(bid=_bid(bdgt_amt=20_000_000_000), match=_match(), interest=None,
                  company_license_codes=set(), company_perf_amounts=[1_000_000_000], now=NOW)
    assert _axis(r, "scale").score < 0.5


def test_no_performance_is_neutral():
    r = score_bid(bid=_bid(), match=_match(), interest=None,
                  company_license_codes=set(), company_perf_amounts=[], now=NOW)
    assert _axis(r, "scale").score == 0.5


# ── 시의성 축 ──────────────────────────────────────────────────────
def test_too_close_deadline_is_penalized():
    """이틀 남은 공고는 준비가 어렵다 — 임박할수록 좋은 게 아니다."""
    urgent = score_bid(bid=_bid(bid_clse_dt=NOW + timedelta(days=1)), match=_match(),
                       interest=None, company_license_codes=set(),
                       company_perf_amounts=[], now=NOW)
    comfortable = score_bid(bid=_bid(bid_clse_dt=NOW + timedelta(days=7)), match=_match(),
                            interest=None, company_license_codes=set(),
                            company_perf_amounts=[], now=NOW)
    assert _axis(urgent, "freshness").score < _axis(comfortable, "freshness").score


# ── 관심도 축 + 합산 ───────────────────────────────────────────────
def test_signal_weight_applied():
    """같은 유사도라도 스크랩이 면허보다 높게 반영된다."""
    scrap = score_bid(bid=_bid(), match=_match(), company_license_codes=set(),
                      company_perf_amounts=[], now=NOW,
                      interest=(0.9, InterestSignal("A", "스크랩 공고")))
    lic = score_bid(bid=_bid(), match=_match(), company_license_codes=set(),
                    company_perf_amounts=[], now=NOW,
                    interest=(0.9, InterestSignal("A", "보유 면허")))
    assert _axis(scrap, "interest").score > _axis(lic, "interest").score


def test_missing_interest_does_not_zero_total():
    """검색에 안 걸려도 다른 축으로 점수가 남는다(사다리 구조가 못 하던 것)."""
    r = score_bid(bid=_bid(), match=_match("가능"), interest=None,
                  company_license_codes=set(), company_perf_amounts=[], now=NOW)
    assert r.total > 0
    assert _axis(r, "interest").score == 0.0


def test_total_stays_in_unit_range():
    """모든 축 만점이어도 1.0을 넘지 않는다(가중치 합 = 1)."""
    r = score_bid(bid=_bid(required_licenses=[{"code": "0037", "name_raw": "x"}]),
                  match=_match("가능", 9, 9), company_license_codes={"0037"},
                  company_perf_amounts=[1_000_000_000], now=NOW,
                  interest=(1.0, InterestSignal("A", "스크랩 공고")))
    assert 0.0 <= r.total <= 1.0
