# -*- coding: utf-8 -*-
"""bid_table ORM 매핑 (읽기 전용).

db/schema/01_bid_table.sql(SSOT)을 미러링한다. 조회 API가 실제로 서빙/필터/정렬에
쓰는 컬럼만 매핑했다(전 컬럼을 옮기지 않는다 — 파이프라인 운영 컬럼은 이 앱의 관심사가
아니다). 스키마가 바뀌면 여기와 SSOT가 갈라지므로, 컬럼 주석에 SSOT 근거를 남긴다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Numeric, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.infra.db.models.bid_tag import (
    LOW_CONFIDENCE_SUFFIX,
    NO_BASIS_SOURCE,
    BidTag,
)
from app.infra.db.session import Base


class Bid(Base):
    __tablename__ = "bid_table"

    # --- 식별 (PK: 복합키. bid_id는 GENERATED STORED 파생 컬럼) ---
    bid_ntce_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    bid_ntce_ord: Mapped[str] = mapped_column(String(10), primary_key=True)
    # bid_id는 DB가 생성하는 값이라 앱은 읽기만 한다. split 금지(불투명 식별자).
    bid_id: Mapped[str] = mapped_column(String(60))
    bid_category: Mapped[str] = mapped_column(String(10))   # NOT NULL

    # --- 목록/상세 공용 기본 필드 (전부 nullable) ---
    bid_ntce_nm: Mapped[str | None] = mapped_column(Text)
    ntce_instt_nm: Mapped[str | None] = mapped_column(String(200))
    dminstt_nm: Mapped[str | None] = mapped_column(String(200))
    # 공고종류(등록공고/재공고/취소공고/변경공고). '취소공고'면 같은 bid_ntce_no의 모든
    # 행(원 공고 포함)을 노출에서 제외한다 — bid_repository.not_canceled_clause() 참고(#140).
    ntce_kind_nm: Mapped[str | None] = mapped_column(String(20))

    # --- 일정 (KST naive TIMESTAMP — 타임존 붙이지 말 것) ---
    bid_ntce_dt: Mapped[datetime | None] = mapped_column()        # today 필터 기준
    bid_clse_dt: Mapped[datetime | None] = mapped_column()        # deadline 정렬 기준
    openg_dt: Mapped[datetime | None] = mapped_column()
    bid_qlfct_rgst_dt: Mapped[datetime | None] = mapped_column()

    # --- 금액 ---
    presmpt_prce: Mapped[int | None] = mapped_column(BigInteger)
    bdgt_amt: Mapped[int | None] = mapped_column(BigInteger)

    # --- 방식 ---
    cntrct_cncls_mthd_nm: Mapped[str | None] = mapped_column(String(50))
    sucsfbid_mthd_nm: Mapped[str | None] = mapped_column(String(200))
    bid_methd_nm: Mapped[str | None] = mapped_column(String(50))
    bid_prtcpt_lmt_yn: Mapped[bool | None] = mapped_column(Boolean)

    # --- 링크 ---
    bid_ntce_dtl_url: Mapped[str | None] = mapped_column(Text)

    # --- 자격요건 (상세 응답 qualification 조립 원천) ---
    company_size_limit: Mapped[str | None] = mapped_column(String(20))
    direct_production_req: Mapped[bool | None] = mapped_column(Boolean)
    credit_rating_req: Mapped[bool | None] = mapped_column(Boolean)
    required_licenses: Mapped[list | None] = mapped_column(JSONB)
    region_limit_type: Mapped[str | None] = mapped_column(String(20))
    region_limit_names: Mapped[list | None] = mapped_column(JSONB)
    performance_reqs: Mapped[list | None] = mapped_column(JSONB)
    personnel_reqs: Mapped[list | None] = mapped_column(JSONB)
    required_certs: Mapped[list | None] = mapped_column(JSONB)
    award_cutline_type: Mapped[str | None] = mapped_column(String(20))
    award_cutline_value: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    tech_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    price_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    joint_venture_allowed: Mapped[bool | None] = mapped_column(Boolean)
    subcontract_allowed: Mapped[bool | None] = mapped_column(Boolean)

    # --- 노출 게이트 ---
    qual_status: Mapped[str | None] = mapped_column(String(20))


# 품목 태그(bidmate-pipeline#97). 상관 서브쿼리를 컬럼처럼 붙여, 목록·검색·상세·추천이
# 모두 Bid를 select하는 것만으로 태그를 함께 받게 한다 — 조회 메서드를 하나도 안 고쳐도
# 되고, relationship + lazy load에서 생기는 N+1도 없다.
#
# 신뢰도가 임계값 미만(source가 _low로 끝남)이거나 근거가 없으면(none) NULL을 준다.
# "미분류"는 공고에 대한 정보가 아니라 우리 분류기의 한계라, 화면에 배지로 띄우면
# 담당자 판단에 도움이 안 되면서 자리만 차지한다(전체의 약 7.9%).
# 이 판단을 여기 한 곳에 두어 프론트는 null 여부만 보면 되게 한다.
#
# ⚠ 통계는 반대다. 미분류를 빼면 분포가 왜곡되므로 별도 항목으로 집계해야 하고,
#   그쪽은 bid_tags를 직접 조회해야 한다(이 프로퍼티를 쓰면 안 된다).
Bid.item_tag = column_property(
    select(BidTag.tag)
    .where(
        BidTag.bid_id == Bid.bid_id,
        BidTag.source != NO_BASIS_SOURCE,
        ~BidTag.source.endswith(LOW_CONFIDENCE_SUFFIX),
    )
    .correlate_except(BidTag)
    .scalar_subquery(),
    deferred=False,
)
