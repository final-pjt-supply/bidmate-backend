# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 ORM 매핑 — companies 하위 8개 테이블(회원가입 시 입력).

이 테이블들은 에이전트팀이 공유 RDS에 직접 생성했다. 우리 앱은 이를 읽기(그리고
추후 쓰기)로 매핑만 하고 DDL은 만들지 않는다 — bid_table과 같은 coexist 규약
(alembic/env.py의 제외 목록 참조, create_all 호출 안 함).

⚠ 복합 PK를 실제 DB와 일치시킨다. company_id만 PK로 잡으면 한 회사의 여러 면허·
품목이 ORM 아이덴티티 맵에서 한 행으로 뭉개진다(라이선스 5건 → 1건).

`*_name` 컬럼은 각 테이블에 이미 비정규화 저장돼 있다(가입 시 마스터에서 채움).
그래서 조회는 마스터 조인 없이 이 테이블만 읽으면 된다.
"""
from datetime import date

from sqlalchemy import BigInteger, Boolean, Date, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class CompanyQualification(Base):
    """기업규모·신용등급 (회사당 1행, 1:1)."""
    __tablename__ = "company_qualifications"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_size: Mapped[str | None] = mapped_column(String(20))
    credit_rating: Mapped[str | None] = mapped_column(String(20))


class CompanyRegion(Base):
    """본점·지사 소재지 (1:N)."""
    __tablename__ = "company_regions"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    region_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    region_name: Mapped[str | None] = mapped_column(String(100))
    region_type: Mapped[str | None] = mapped_column(String(20))   # 본점/지사


class CompanyLicense(Base):
    """보유 면허·업종 (1:N)."""
    __tablename__ = "company_licenses"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    license_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    license_name: Mapped[str | None] = mapped_column(String(200))


class CompanyItem(Base):
    """조달 품목·직접생산 (1:N)."""
    __tablename__ = "company_items"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    item_name: Mapped[str | None] = mapped_column(String(200))
    has_direct_production: Mapped[bool | None] = mapped_column(Boolean)
    direct_prod_valid_until: Mapped[date | None] = mapped_column(Date)


class CompanyCert(Base):
    """보유 인증 (1:N)."""
    __tablename__ = "company_certs"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cert_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    cert_name: Mapped[str | None] = mapped_column(String(200))
    valid_until: Mapped[date | None] = mapped_column(Date)


class CompanyPersonnel(Base):
    """기술인력 — 자격·등급별 보유 인원 (1:N)."""
    __tablename__ = "company_personnel"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    qual_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    qual_name: Mapped[str | None] = mapped_column(String(200))
    headcount: Mapped[int | None] = mapped_column(SmallInteger)


class CompanyCapacityEval(Base):
    """시공능력평가액 — 면허별 (1:N)."""
    __tablename__ = "company_capacity_evals"

    company_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    license_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    license_name: Mapped[str | None] = mapped_column(String(200))
    eval_amount: Mapped[int | None] = mapped_column(BigInteger)   # 원 단위
    eval_year: Mapped[int | None] = mapped_column(SmallInteger)


class CompanyPerformanceRecord(Base):
    """계약 실적 — 한 건씩 입력하는 대장 (1:N, surrogate PK)."""
    __tablename__ = "company_performance_records"

    record_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(BigInteger, index=True)
    contract_name: Mapped[str | None] = mapped_column(String(300))
    field_code: Mapped[str | None] = mapped_column(String(20))
    field_name: Mapped[str | None] = mapped_column(String(200))
    contract_amt: Mapped[int | None] = mapped_column(BigInteger)   # 원 단위
    end_date: Mapped[date | None] = mapped_column(Date)
