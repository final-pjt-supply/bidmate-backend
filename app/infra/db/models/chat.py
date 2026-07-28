# -*- coding: utf-8 -*-
"""챗봇 대화 영속화 ORM — 세션/메시지/검색이력 (백엔드 소유, Alembic 관리).

ADR-22: 인메모리 세션(ADR 0005)을 RDS로 영속화. '되읽는 제품 데이터'만 RDS에 둔다
— 관측 로그는 CloudWatch, 검색 상세는 S3(성격이 다름).

- session_id = UUID: 클라 노출 ID(bigint autoincrement의 enumerable 회피, 현 코드도 uuid).
- session_context = ADR 0005 왕복 컨텍스트(불투명)를 JSONB로 보관. 없으면 문맥이 안 이어짐.
- FK ON DELETE CASCADE: 회사 탈퇴(파기) 시 대화가 함께 삭제(개보법 삭제권).
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class ChatSession(Base):
    """대화방. company_id로 격리, updated_at으로 최근순 정렬."""
    __tablename__ = "chat_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    session_context: Mapped[dict | None] = mapped_column(JSONB)   # ADR 0005 왕복 컨텍스트(불투명)
    title: Mapped[str | None] = mapped_column(String(200))        # 첫 메시지로 자동 생성 or null
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_sessions_company_recent", "company_id", "updated_at"),
    )


class ChatMessage(Base):
    """대화 메시지(입출력). 이 행들의 시간순 나열이 사람이 보는 대화 이력."""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)   # user / assistant
    content: Mapped[str | None] = mapped_column(Text)              # user=질문, assistant=answer
    # assistant 전용: {action, citations, redirect_filters} — 근거공고·리다이렉트 재현용
    response_meta: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_messages_session_order", "session_id", "created_at"),
    )


class SearchHistory(Base):
    """검색 이력 — 사용자에게 보여주는 가벼운 부분만(상세는 S3).

    session_id: 챗봇발이면 세션 연결, 검색창발이면 null. 세션 삭제 시 SET NULL
    (이력 자체는 남긴다 — 회사 탈퇴 때만 company CASCADE로 삭제).
    """
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_sessions.session_id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)   # search_ui/chat_agent/api
    user_query: Mapped[str] = mapped_column(Text, nullable=False)
    result_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_search_company_recent", "company_id", "created_at"),
    )
