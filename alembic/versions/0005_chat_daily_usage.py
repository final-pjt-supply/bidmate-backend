# -*- coding: utf-8 -*-
"""chat daily usage counter — 회사×날짜별 챗 호출 수(레이트리밋 일일 상한)

백엔드 소유 신규 테이블. 인메모리 일일 카운터는 재배포마다 리셋돼 무의미하므로
지속 저장이 필요해 RDS에 둔다. UPSERT로 원자 증가.

Revision ID: 0005_chat_daily_usage
Revises: 0004_chat_session_tables
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005_chat_daily_usage"
down_revision: Union[str, None] = "0004_chat_session_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_daily_usage",
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("company_id", "usage_date"),
    )


def downgrade() -> None:
    op.drop_table("chat_daily_usage")
