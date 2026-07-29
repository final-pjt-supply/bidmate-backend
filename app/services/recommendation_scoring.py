# -*- coding: utf-8 -*-
"""추천 점수 계산 — 축별 점수와 가중 합산.

기존 구조의 문제:
  관심 신호를 '사다리'로 골랐다(스크랩 있으면 스크랩만, 없으면 실적만, …).
  한 축만 쓰므로 스크랩 하나를 지우면 추천이 면허 기준으로 통째로 뒤집혔고,
  점수도 코사인 유사도 하나뿐이라 "왜 이게 위에 있는지" 설명할 수 없었다.

여기서는 축을 나눠 각각 0~1로 정규화하고 가중 합산한다. 신호 하나가 사라져도
나머지 축이 순위를 지탱한다.

축을 나눈 기준은 '무엇으로 재는가'다 — 벡터 유사도로 재야 하는 것(관심도)과
집합·수치 비교로 재야 하는 것(면허 일치, 예산 규모)을 섞으면 둘 다 나빠진다.
면허는 "비슷한가"가 아니라 "있는가/없는가"이므로 코드 정합으로 잰다.

가중치는 아직 근거 없는 초기값이다. 골든셋(회사별 '이 공고가 위에 와야 한다'
목록)으로 조정해야 하며, 그 전까지는 이 파일 상단만 고치면 되도록 모아 두었다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# ── 가중치 (합 1.0) ────────────────────────────────────────────────
# 관심도를 가장 크게 둔 이유: 회사가 직접 남긴 신호(스크랩·실적)라 의도가 가장
# 분명하다. 자격은 그다음 — '가능'이 후보의 3%뿐이라 위로 올리는 것만으로도
# 체감이 크다. 규모·시의성은 보조라 낮게 둔다.
W_INTEREST = 0.35   # 관심도  — 스크랩·실적 제목 벡터 유사도
W_ELIGIBLE = 0.25   # 자격    — verdict + 축 충족률
W_CAPABILITY = 0.20  # 역량   — 면허 코드 정합
W_SCALE = 0.10      # 규모    — 예산 대비 실적 규모
W_FRESH = 0.10      # 시의성  — 마감 임박도

# 관심 신호별 배수. 스크랩은 회사가 직접 담은 것이라 실적보다 의도가 분명하다.
# 품목·면허는 텍스트가 짧고 후보 대부분에 걸려 변별력이 낮아 더 낮춘다.
SIGNAL_WEIGHT = {
    "스크랩 공고": 1.0,
    "실적 계약명": 0.7,
    "취급 품목": 0.5,
    "보유 면허": 0.4,
}

# verdict → 자격 점수. '불가'는 후보에서 이미 빠지므로 여기 없다.
# '확인필요'가 후보의 대부분(3,395/4,306)이라 낮게 둬야 '가능'이 올라온다.
_VERDICT_SCORE = {"가능": 1.0, "보완가능": 0.6, "확인필요": 0.25}

# 마감이 이 일수 이내면 시의성 만점에서 선형 감소. 너무 임박하면 준비가 불가능해
# 오히려 감점한다(아래 _freshness 참조).
_URGENT_DAYS = 14
_TOO_LATE_DAYS = 2

# name_raw에 박힌 업종코드. required_licenses.code가 비어 있는 경우가 많아
# (21,776개 중 code 보유 7,663개) 텍스트에서도 뽑아 커버리지를 넓힌다.
# 예: "소프트웨어사업자(컴퓨터관련서비스사업)(1468)" → 1468
_CODE_IN_TEXT = re.compile(r"\((\d{3,10})\)")


@dataclass(frozen=True)
class InterestSignal:
    """관심 텍스트 한 건과 그 출처."""

    text: str
    source: str            # SIGNAL_WEIGHT의 키

    @property
    def weight(self) -> float:
        return SIGNAL_WEIGHT.get(self.source, 0.4)


@dataclass
class AxisScore:
    """축 하나의 점수와 사람이 읽을 근거."""

    key: str
    score: float           # 0~1
    reason: str = ""


@dataclass
class ScoreBreakdown:
    total: float
    axes: list[AxisScore] = field(default_factory=list)
    matched_text: str = ""      # 관심도를 만든 텍스트(화면 표시용)
    matched_source: str = ""


def extract_license_codes(required_licenses) -> set[str]:
    """공고가 요구하는 업종코드 집합.

    code 필드를 우선 쓰고, 비어 있으면 name_raw에서 괄호 안 숫자를 뽑는다.
    둘 다 없으면(자연어로만 서술된 요건) 이 공고는 역량 축을 평가할 수 없다.
    """
    codes: set[str] = set()
    for item in required_licenses or []:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if code:
            codes.add(str(code).strip())
            continue
        found = _CODE_IN_TEXT.findall(str(item.get("name_raw") or ""))
        codes.update(found)
    return codes


def _capability(company_license_codes: set[str], required: set[str]) -> AxisScore:
    """면허 정합 — '비슷한가'가 아니라 '있는가'라서 집합 연산으로 잰다."""
    if not required:
        # 요구 면허를 알 수 없는 공고. 0점을 주면 '요건이 명시된 공고'가 일방적으로
        # 불리해지므로 중립(0.5)으로 둔다 — 모르는 것과 못 하는 것은 다르다.
        return AxisScore("capability", 0.5, "요구 면허 정보 없음")
    if not company_license_codes:
        return AxisScore("capability", 0.0, "보유 면허 미등록")

    hit = company_license_codes & required
    ratio = len(hit) / len(required)
    if hit:
        return AxisScore("capability", ratio, f"요구 면허 {len(required)}개 중 {len(hit)}개 보유")
    return AxisScore("capability", 0.0, "요구 면허 미보유")


def _eligibility(match) -> AxisScore:
    """자격 판정 — verdict를 기본으로 하고 축 충족률로 보정한다.

    verdict만 쓰면 같은 '확인필요' 안에서 9축 중 8개를 채운 공고와 1개만 채운
    공고가 동점이 된다. 충족률을 절반 섞어 그 안에서도 순서가 생기게 한다.
    """
    verdict = getattr(match, "verdict", None)
    base = _VERDICT_SCORE.get(verdict or "", 0.25)

    required = getattr(match, "required", None) or 0
    satisfied = getattr(match, "satisfied", None) or 0
    if required > 0:
        rate = min(1.0, satisfied / required)
        score = base * 0.5 + rate * 0.5
        return AxisScore(
            "eligibility", score, f"{verdict or '판정 없음'} · {required}축 중 {satisfied}축 충족"
        )
    return AxisScore("eligibility", base, verdict or "판정 없음")


def _scale(bid, company_perf_amounts: list[int]) -> AxisScore:
    """규모 적합 — 회사가 감당해 본 규모와 공고 예산의 거리.

    실적이 없으면 판단 근거가 없으므로 중립(0.5). 있으면 '최대 실적'을 기준으로
    삼는다(평균은 소액 실적이 많은 회사를 과소평가한다).
    """
    budget = getattr(bid, "bdgt_amt", None) or getattr(bid, "presmpt_prce", None)
    if not budget or not company_perf_amounts:
        return AxisScore("scale", 0.5, "규모 비교 불가")

    capacity = max(company_perf_amounts)
    if capacity <= 0:
        return AxisScore("scale", 0.5, "규모 비교 불가")

    ratio = budget / capacity
    # 0.3~2배 구간이 '해봤던 규모' — 만점. 벗어날수록 감점하되 0 아래로는 안 간다.
    if 0.3 <= ratio <= 2.0:
        score = 1.0
    elif ratio < 0.3:
        score = max(0.3, ratio / 0.3)          # 너무 작은 건: 할 수는 있으나 매력도 낮음
    else:
        score = max(0.0, 1.0 - (ratio - 2.0) / 8.0)   # 너무 큰 건: 수행 부담
    return AxisScore("scale", score, f"예산 {budget:,}원 / 최대 실적 {capacity:,}원")


def _freshness(bid, now: datetime) -> AxisScore:
    """시의성 — 준비할 시간이 남았는가.

    임박할수록 좋은 게 아니다. 이틀 남은 공고는 사실상 참여가 어려우므로,
    '충분히 남았지만 너무 멀지도 않은' 구간을 높게 본다.
    """
    close = getattr(bid, "bid_clse_dt", None)
    if close is None:
        return AxisScore("freshness", 0.5, "마감 미정")

    days = (close - now).total_seconds() / 86400
    if days < 0:
        return AxisScore("freshness", 0.0, "마감됨")
    if days <= _TOO_LATE_DAYS:
        return AxisScore("freshness", 0.3, f"마감 임박(D-{int(days)}) · 준비 시간 부족")
    if days <= _URGENT_DAYS:
        return AxisScore("freshness", 1.0, f"D-{int(days)}")
    # 멀수록 완만히 감소 — 지금 당장 볼 이유가 적다.
    return AxisScore("freshness", max(0.4, 1.0 - (days - _URGENT_DAYS) / 60), f"D-{int(days)}")


def score_bid(
    *,
    bid,
    match,
    interest: tuple[float, InterestSignal] | None,
    company_license_codes: set[str],
    company_perf_amounts: list[int],
    now: datetime,
) -> ScoreBreakdown:
    """공고 하나의 축별 점수와 가중 합산 결과.

    interest는 (원점수, 신호) — 검색에서 걸리지 않았으면 None이다. 이때 관심도는
    0이지만 다른 축은 그대로 계산한다. 그래야 스크랩이 하나도 없는 회원도
    자격·역량·규모만으로 추천을 받는다(사다리 구조가 못 하던 것).
    """
    axes: list[AxisScore] = []

    if interest is None:
        axes.append(AxisScore("interest", 0.0, "관심 신호와 직접 일치 없음"))
        matched_text = matched_source = ""
    else:
        raw, signal = interest
        # 신호 종류별 배수를 곱해 약한 신호가 강한 신호를 덮지 못하게 한다.
        axes.append(
            AxisScore("interest", min(1.0, max(0.0, raw)) * signal.weight, signal.source)
        )
        matched_text, matched_source = signal.text, signal.source

    axes.append(_eligibility(match))
    axes.append(_capability(company_license_codes, extract_license_codes(
        getattr(bid, "required_licenses", None))))
    axes.append(_scale(bid, company_perf_amounts))
    axes.append(_freshness(bid, now))

    weights = {
        "interest": W_INTEREST,
        "eligibility": W_ELIGIBLE,
        "capability": W_CAPABILITY,
        "scale": W_SCALE,
        "freshness": W_FRESH,
    }
    total = sum(a.score * weights[a.key] for a in axes)
    return ScoreBreakdown(
        total=round(total, 6),
        axes=axes,
        matched_text=matched_text,
        matched_source=matched_source,
    )
