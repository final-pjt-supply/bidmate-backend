# -*- coding: utf-8 -*-
"""companies.deleted_at 추가 — 회원 탈퇴 소프트 삭제

Revision ID: 0002_companies_deleted_at
Revises: 0001_baseline
Create Date: 2026-07-24

탈퇴를 즉시 하드 삭제하지 않고 시각만 기록한다(30일 보관 후 파기 안내).
NULL = 활성 회원이므로 기존 행은 자동으로 활성으로 남는다(백필 불필요).

⚠ 공유 RDS 대상 마이그레이션 — 팀 협의 완료 후 수행(alembic/README.md).
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_companies_deleted_at"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    # 활성 회원 조회(deleted_at IS NULL)가 모든 인증 요청 경로에 들어가므로 부분 인덱스를 둔다.
    op.create_index(
        "ix_companies_active",
        "companies",
        ["id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_companies_active", table_name="companies")
    op.drop_column("companies", "deleted_at")
