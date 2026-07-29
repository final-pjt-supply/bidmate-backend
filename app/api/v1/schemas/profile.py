# -*- coding: utf-8 -*-
"""GET /me/profile 응답 스키마 — 회사 자격요건 프로필 8개 섹션.

각 섹션은 ORM 행에서 바로 조립한다(from_attributes). 값은 관대하게(Optional) 둔다
— 데모/부분입력 데이터 한 건 때문에 프로필 전체 조회가 500 나면 안 된다.
`_name`은 DB에 비정규화 저장돼 있어 그대로 노출한다(마스터 조인 없음).
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Section(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class QualificationOut(_Section):
    company_size: str | None = None
    credit_rating: str | None = None


class RegionOut(_Section):
    region_code: str
    region_name: str | None = None
    region_type: str | None = None


class LicenseOut(_Section):
    license_code: str
    license_name: str | None = None


class ItemOut(_Section):
    item_code: str
    item_name: str | None = None
    has_direct_production: bool | None = None
    direct_prod_valid_until: date | None = None


class CertOut(_Section):
    cert_code: str
    cert_name: str | None = None
    valid_until: date | None = None


class PersonnelOut(_Section):
    qual_code: str
    qual_name: str | None = None
    headcount: int | None = None


class CapacityEvalOut(_Section):
    license_code: str
    license_name: str | None = None
    eval_amount: int | None = None
    eval_year: int | None = None


class PerformanceRecordOut(_Section):
    record_id: int
    contract_name: str | None = None
    field_code: str | None = None
    field_name: str | None = None
    contract_amt: int | None = None
    end_date: date | None = None


class ProfileResponse(BaseModel):
    """회사 자격요건 프로필 전체.

    qualification은 1:1이라 없을 수 있어 Optional(가입 직후 미입력). 나머지는
    1:N이라 없으면 빈 배열로 준다 — 프론트가 null 분기 없이 렌더한다.
    """
    company_id: str
    qualification: QualificationOut | None = None
    regions: list[RegionOut] = []
    licenses: list[LicenseOut] = []
    items: list[ItemOut] = []
    certs: list[CertOut] = []
    personnel: list[PersonnelOut] = []
    capacity_evals: list[CapacityEvalOut] = []
    performance_records: list[PerformanceRecordOut] = []


# ── 입력(PUT /me/profile) ─────────────────────────────────────────────
# 클라는 code만 보낸다 — _name은 서버가 마스터 조회로 채운다(신뢰경계 서버).
# extra="forbid": 클라가 region_name 등 파생값을 밀어넣지 못하게 막는다(422).
class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


# 필수/선택·허용값은 DB 제약(NOT NULL·CHECK)에 맞춘다 — 여기서 안 막으면 insert가
# 500(CHECK 위반)난다. 허용값이 정해진 컬럼은 Literal로 422로 돌린다.
class QualificationIn(_Input):
    company_size: Literal["small", "medium", "mid_large", "conglomerate"]   # CHECK
    credit_rating: str | None = Field(default=None, max_length=10)   # DB: varchar(10)


class RegionIn(_Input):
    region_code: str = Field(min_length=1, max_length=20)
    region_type: Literal["hq", "branch"]   # CHECK (본점/지사)


class LicenseIn(_Input):
    license_code: str = Field(min_length=1, max_length=20)


class ItemIn(_Input):
    item_code: str = Field(min_length=1, max_length=20)
    has_direct_production: bool = False   # NOT NULL(기본 false)
    direct_prod_valid_until: date | None = None


class CertIn(_Input):
    cert_code: str = Field(min_length=1, max_length=20)
    valid_until: date | None = None


class PersonnelIn(_Input):
    qual_code: str = Field(min_length=1, max_length=20)
    headcount: int = Field(gt=0)   # NOT NULL, CHECK(headcount > 0)


class CapacityEvalIn(_Input):
    license_code: str = Field(min_length=1, max_length=20)
    eval_amount: int = Field(ge=0)   # NOT NULL, 원 단위
    eval_year: int | None = Field(default=None, ge=1900, le=2100)


class PerformanceRecordIn(_Input):
    # field_code는 license_master를 참조한다(FK) — 실적 분야코드 = 면허/업종코드.
    # 그래서 field_name도 서버가 license_master에서 채운다(다른 섹션과 동일). 선택.
    contract_name: str = Field(min_length=1, max_length=300)   # NOT NULL
    contract_amt: int = Field(ge=0)                            # NOT NULL
    end_date: date                                            # NOT NULL
    field_code: str | None = Field(default=None, max_length=20)


class ProfileUpsertRequest(BaseModel):
    """전체 프로필 저장(full replace). 회원가입 폼이 완성된 프로필을 한 번에 보낸다.

    누락한(=보내지 않은) 섹션은 빈 값으로 본다 — 이 요청이 곧 '원하는 최종 상태'다.
    같은 섹션에 code 중복이 있으면 422(테이블 PK가 (company_id, code)라 불가).
    """
    model_config = ConfigDict(extra="forbid")

    # 배열별 최대 항목 수 — 요청 한 번으로 수만 건을 밀어넣는 남용을 막는다.
    # 정상 회사 데이터를 넉넉히 덮되(실적 대장이 가장 많음) 위로 상한을 둔다.
    qualification: QualificationIn | None = None
    regions: list[RegionIn] = Field(default_factory=list, max_length=30)
    licenses: list[LicenseIn] = Field(default_factory=list, max_length=100)
    items: list[ItemIn] = Field(default_factory=list, max_length=200)
    certs: list[CertIn] = Field(default_factory=list, max_length=50)
    personnel: list[PersonnelIn] = Field(default_factory=list, max_length=100)
    capacity_evals: list[CapacityEvalIn] = Field(default_factory=list, max_length=100)
    performance_records: list[PerformanceRecordIn] = Field(
        default_factory=list, max_length=500)
