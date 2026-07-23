# -*- coding: utf-8 -*-
"""user_events — 고객 여정 이벤트 (append-only)

Revision ID: 0002_user_events
Revises: 0001_baseline
Create Date: 2026-07-23

API 서버 소유 첫 실제 테이블. soft ref라 FK 제약은 걸지 않는다(로그 쓰기가 본
트랜잭션을 막지 않게). 인덱스는 여정 재구성·집계 질의에 맞춘다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_user_events"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), nullable=True),
        sa.Column("anonymous_id", sa.String(length=64), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("event_name", sa.String(length=60), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("page", sa.String(length=40), nullable=True),
        sa.Column("referrer_page", sa.String(length=40), nullable=True),
        sa.Column("bid_id", sa.String(length=60), nullable=True),
        sa.Column("chat_session_id", sa.BigInteger(), nullable=True),
        sa.Column("query_log_id", sa.BigInteger(), nullable=True),
        sa.Column("properties", postgresql.JSONB(), nullable=True),
        sa.Column("device_type", sa.String(length=20), nullable=True),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_events_company_time", "user_events", ["company_id", "created_at"])
    op.create_index("idx_events_anon_time", "user_events", ["anonymous_id", "created_at"])
    op.create_index("idx_events_name_time", "user_events", ["event_name", "created_at"])
    op.create_index("idx_events_visit_order", "user_events", ["visit_id", "created_at"])
    op.create_index("idx_events_bid", "user_events", ["bid_id"])


def downgrade() -> None:
    op.drop_table("user_events")   # 인덱스도 함께 드랍됨
