# -*- coding: utf-8 -*-
"""표준코드 마스터 ORM — 프로필 입력 시 code→name 조회 겸 코드 검증에 쓴다.

에이전트팀이 공유 RDS에 적재한 참조 데이터(읽기 전용, coexist). 여기선 code·name
두 컬럼만 매핑한다(name 채움에 그만큼만 필요). 나머지 컬럼(law_basis, parent_code
등)은 이 앱이 안 쓰므로 매핑하지 않는다.
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base


class RegionMaster(Base):
    __tablename__ = "region_master"
    region_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    region_name: Mapped[str | None] = mapped_column(String(100))


class LicenseMaster(Base):
    __tablename__ = "license_master"
    license_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    license_name: Mapped[str | None] = mapped_column(String(200))


class ItemCodeMaster(Base):
    """품목 마스터. 코드가 2계층이다 — 8자리(물품분류) 아래 10자리(세부품명).
    공고는 사실상 10자리만 쓰므로(참조 8,586건 중 8,532건) 자동완성도 10자리만 노출한다.
    8자리로 등록하면 코드 완전일치 매칭에서 10자리 요구 공고에 안 걸린다."""

    __tablename__ = "item_code_master"
    item_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    item_name: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool | None] = mapped_column(Boolean)


class PersonnelGradeMaster(Base):
    __tablename__ = "personnel_grade_master"
    qual_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    qual_name: Mapped[str | None] = mapped_column(String(200))


class CertMaster(Base):
    __tablename__ = "cert_master"
    cert_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    cert_name: Mapped[str | None] = mapped_column(String(200))
