# -*- coding: utf-8 -*-
"""company_bid_scraps 추가 — 회원이 담은 공고(스크랩)

Revision ID: 0003_company_bid_scraps
Revises: 0002_companies_deleted_at
Create Date: 2026-07-25

회사↔공고 다대다 연결 테이블. match_results와 동일한 복합키 패턴이다
(company_id + 공고 PK). 스크랩은 (회사, 공고) 한 쌍이 유일하므로 복합 PK로
중복을 DB가 막고, INSERT ... ON CONFLICT DO NOTHING으로 멱등 처리한다.

⚠ 공유 RDS 대상 마이그레이션 — 팀 협의 완료 후 수행(alembic/README.md).
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_company_bid_scraps"
down_revision: Union[str, None] = "0002_companies_deleted_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_bid_scraps",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("bid_ntce_no", sa.String(length=40), nullable=False),
        sa.Column("bid_ntce_ord", sa.String(length=10), nullable=False),
        # KST naive로 저장한다 — RDS 기본 타임존은 UTC라 now()만 쓰면 9시간 어긋난다.
        # 이 프로젝트의 다른 일시 컬럼(match_results.computed_at 등)이 KST naive라 맞춘다.
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'Asia/Seoul')"),
            nullable=False,
        ),
        # 회사 삭제(30일 후 파기 배치)면 그 회사의 스크랩도 함께 정리된다.
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        # 공고 PK를 참조. 존재하지 않는 공고는 담을 수 없다.
        sa.ForeignKeyConstraint(
            ["bid_ntce_no", "bid_ntce_ord"],
            ["bid_table.bid_ntce_no", "bid_table.bid_ntce_ord"],
        ),
        # (회사, 공고) 한 쌍이 유일 — 중복 스크랩 방지 + ON CONFLICT 멱등.
        sa.PrimaryKeyConstraint("company_id", "bid_ntce_no", "bid_ntce_ord"),
    )
    # 목록은 "내 스크랩을 최신순"으로 조회한다. PK 인덱스는 (company_id, no, ord)
    # 순서라 created_at 정렬을 못 타므로 별도 인덱스를 둔다.
    op.create_index(
        "ix_scraps_company_created",
        "company_bid_scraps",
        ["company_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scraps_company_created", table_name="company_bid_scraps")
    op.drop_table("company_bid_scraps")
