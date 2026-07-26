# -*- coding: utf-8 -*-
"""운영 연결 없이 추천 검증 스크립트의 점수 조립만 고정한다."""

import sys
from unittest.mock import MagicMock

import pytest

# 이 테스트는 순수 점수 함수만 검증한다. 로컬 libpq가 없어도 수집되게 DB
# 드라이버를 대체하며, 실제 연결 경로는 의도적으로 실행하지 않는다.
sys.modules.setdefault("psycopg", MagicMock())

from scripts.try_recommend import cosine, hybrid_score, title_scores


def test_cosine_rejects_dimension_mismatch():
    with pytest.raises(ValueError, match="벡터 차원 불일치"):
        cosine([1.0, 0.0], [1.0])


def test_title_scores_keeps_best_query_and_reason():
    scores, reasons = title_scores(
        ["CCTV", "네트워크"],
        [[1.0, 0.0], [0.0, 1.0]],
        {"a": "영상감시", "b": "스위치"},
        [[0.9, 0.1], [0.2, 0.8]],
    )

    assert scores["a"] == pytest.approx(cosine([1.0, 0.0], [0.9, 0.1]))
    assert reasons == {"a": "CCTV", "b": "네트워크"}


def test_hybrid_score_uses_configured_title_weight():
    assert hybrid_score(0.6, 0.8, 0.8) == pytest.approx(0.64)
