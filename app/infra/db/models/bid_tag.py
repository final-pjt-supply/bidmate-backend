# -*- coding: utf-8 -*-
"""bid_tags ORM 매핑 (읽기 전용).

파이프라인(bidmate-pipeline#97)이 공고 제목으로 품목 태그를 정해 쌓는 테이블이다.
업종(공사/용역/물품/외자) 아래 단계 분류로, 이 앱은 읽기만 한다 — 쓰는 쪽은
realtime-merge Lambda다.

bid_table에 컬럼을 붙이지 않고 따로 둔 이유는 파이프라인 쪽 결정이다: bid_table은
여러 파이프라인이 함께 쓰는 테이블이라 컬럼 추가의 영향 범위를 예측하기 어렵다.
외래키도 걸려 있지 않다.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.session import Base

# 태그를 무엇으로 정했는지. 정확도가 달라 통계에서 구분할 수 있어야 한다.
#   cnstty_column   공사 main_cnstty_nm 컬럼 (발주기관 입력값)
#   code            조달청 세부품명번호/업종코드 매핑 (정답)
#   model_thng      물품 모델 (Macro F1 0.813)
#   model_servc     용역 모델 (Macro F1 0.840)
#   model_frgcpt    외자에 물품 모델 적용 (정답 라벨이 없어 정확도 미측정)
#   model_*_low     위와 같으나 모델 신뢰도가 임계값 미만
#   none            태그를 정할 근거가 없음(제목 없음, 공종명 없음)
LOW_CONFIDENCE_SUFFIX = "_low"
NO_BASIS_SOURCE = "none"


class BidTag(Base):
    __tablename__ = "bid_tags"

    bid_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    tag: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(30))
    # 모델 추론일 때만 값이 있다. 1위와 2위 태그의 점수차이며 확률이 아니다.
    # 모델별로 척도가 달라 업종을 넘어 비교하면 안 된다.
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
