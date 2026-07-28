# -*- coding: utf-8 -*-
"""chat session persistence — chat_sessions / chat_messages / search_history

백엔드 소유 신규 테이블(ADR-22). 인메모리 세션을 RDS로 영속화.

Revision ID: 0004_chat_session_tables
Revises: 0003_company_bid_scraps
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_chat_session_tables"
down_revision: Union[str, None] = "0003_company_bid_scraps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id", sa.BigInteger(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("session_context", postgresql.JSONB(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_sessions_company_recent", "chat_sessions", ["company_id", "updated_at"]
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.Uuid(),
            sa.ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("response_meta", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_messages_session_order", "chat_messages", ["session_id", "created_at"]
    )

    op.create_table(
        "search_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "company_id", sa.BigInteger(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "session_id", sa.Uuid(),
            sa.ForeignKey("chat_sessions.session_id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("user_query", sa.Text(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_search_company_recent", "search_history", ["company_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_search_company_recent", table_name="search_history")
    op.drop_table("search_history")
    op.drop_index("idx_messages_session_order", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("idx_sessions_company_recent", table_name="chat_sessions")
    op.drop_table("chat_sessions")
