# -*- coding: utf-8 -*-
"""CompanyRepository의 판단 부분을 DB 없이 Session 스텁으로 고정(QA 후속).

실제 SQL 동등성은 로컬 Postgres 통합 테스트 몫 — 운영 RDS엔 쓰기 테스트를 하지 않는다
(test_bid_repository_integration.py 참고). 여기서는:
  - get_or_create가 동시 첫 요청의 유니크 충돌(IntegrityError)에서 기존 행으로 수렴
  - 수렴 대상이 없으면 원인을 숨기지 않고 재던진다
  - soft_delete가 KST naive 시각으로 deleted_at을 기록한다(전역 tz 규약)
"""
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.infra.db.repositories.company_repository import CompanyRepository


class StubSession:
    """SQLAlchemy Session 최소 대역 — scalars/add/commit/rollback/refresh."""

    def __init__(self, scalar_results, commit_raises=False):
        self._scalars = list(scalar_results)   # scalars().first()가 순서대로 돌려줄 값
        self._commit_raises = commit_raises     # 첫 commit에서 IntegrityError를 낸다
        self.added = []
        self.commits = 0
        self.rolled_back = 0

    def scalars(self, _stmt):
        val = self._scalars.pop(0) if self._scalars else None
        return SimpleNamespace(first=lambda: val)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        if self._commit_raises:
            self._commit_raises = False
            raise IntegrityError("INSERT INTO companies ...", {}, Exception("duplicate key"))
        self.commits += 1

    def rollback(self):
        self.rolled_back += 1

    def refresh(self, _obj):
        pass


def test_get_or_create_converges_on_concurrent_insert():
    """동시 첫 요청이 먼저 만든 경우(commit IntegrityError) → 그 행으로 수렴(500 아님)."""
    existing = SimpleNamespace(id=5, cognito_sub="sub-1", email=None)
    # [최초 cognito 조회=None, 충돌 후 재조회=existing]
    sess = StubSession([None, existing], commit_raises=True)
    repo = CompanyRepository(sess)

    result = repo.get_or_create(cognito_sub="sub-1", email=None, name="이름 미등록")

    assert result is existing          # 새로 만들지 않고 기존 행 반환
    assert sess.rolled_back == 1        # 충돌 시 롤백


def test_get_or_create_reraises_when_no_convergence():
    """IntegrityError인데 재조회로도 못 찾으면(다른 제약 위반) 원인을 숨기지 않고 재던진다."""
    sess = StubSession([None, None], commit_raises=True)
    repo = CompanyRepository(sess)
    with pytest.raises(IntegrityError):
        repo.get_or_create(cognito_sub="sub-1", email=None, name="x")


def test_get_or_create_returns_existing_by_cognito_without_insert():
    """이미 그 cognito_sub 행이 있으면 조회만 하고 만들지 않는다(경합 경로 미진입)."""
    existing = SimpleNamespace(id=7, cognito_sub="sub-1", email="a@b.com")
    sess = StubSession([existing])
    repo = CompanyRepository(sess)
    result = repo.get_or_create(cognito_sub="sub-1", email="a@b.com", name="x")
    assert result is existing
    assert sess.added == [] and sess.commits == 0


def test_soft_delete_stamps_kst_naive():
    """soft_delete는 KST naive 시각으로 deleted_at을 찍는다(서버 로컬시각 아님)."""
    company = SimpleNamespace(id=1, deleted_at=None)
    sess = StubSession([company])
    repo = CompanyRepository(sess)

    assert repo.soft_delete(1) is True
    assert company.deleted_at is not None
    assert company.deleted_at.tzinfo is None      # naive
    assert sess.commits == 1


def test_soft_delete_missing_is_false():
    """없는 회사면 False(commit 없음)."""
    sess = StubSession([None])
    repo = CompanyRepository(sess)
    assert repo.soft_delete(999) is False
    assert sess.commits == 0
