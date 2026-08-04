# -*- coding: utf-8 -*-
"""companies ORM 매핑 — 회사 = 계정.

인증은 Cognito가 담당하므로 이 테이블에 비밀번호는 없다. Cognito 사용자와의
연결고리는 cognito_sub(토큰의 sub 클레임)이며, 우리 쪽 정본 식별자는 id
(= company_id)로 모든 회사 데이터(company_* 9테이블)가 이 id로 격리된다.

⚠ 이 ORM은 공유 RDS 스키마의 '반영'이지 원본이 아니다 — 테이블 생성/변경은
팀 협의 + Alembic으로만 한다(alembic/README.md). 이 앱은 create_all을 부르지 않는다.

companies는 company_* 프로필(에이전트 소유, coexist 제외)과 달리 **백엔드가 Alembic으로
관리**한다(env.py 제외목록에 없음). 그래서 autogenerate가 이 모델↔라이브를 비교하는데,
모델이 라이브 8컬럼(id·name·biz_reg_no·email·cognito_sub·created_at·updated_at·deleted_at)을
모두 매핑함을 확인했다(QA #10, 2026-07 라이브 대조) → 매핑 누락에 의한 DROP 위험 없음.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))  # NOT NULL — 가입 시 회사명

    biz_reg_no: Mapped[str | None] = mapped_column(String(10))  # 하이픈 없는 10자리
    email: Mapped[str | None] = mapped_column(String(255))  # 로그인 식별자(Cognito와 동일)
    # Cognito 사용자 고유 id(sub). 로그인한 토큰과 이 행을 잇는 키.
    cognito_sub: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 탈퇴 시각(소프트 삭제). NULL이면 활성 회원.
    # 즉시 파기하지 않는 이유: 실수 복구·분쟁 대응 여지를 남기기 위함(30일 보관 안내).
    # 조회는 항상 deleted_at IS NULL 조건을 건다 — 탈퇴 회사가 되살아나면 안 된다.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
