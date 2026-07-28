# -*- coding: utf-8 -*-
"""Alembic 마이그레이션 환경 — BidMate API 서버 전용.

핵심 규약(alembic/README.md 참조):
- 관리 대상 = 이 레포가 모델로 매핑한 테이블뿐(Base.metadata에 등록된 것).
- 제외 = 그 밖의 모든 테이블. 운영 DB에는 파이프라인·에이전트 팀이 raw SQL로 만든
  테이블이 다수 있다(마스터 6종, bid_require_* 11종, company_* 등). autogenerate는
  "코드가 정답"으로 전제하므로, 모델에 없는 테이블을 잉여물로 보고 DROP하려 든다.
  bid_table처럼 컬럼 일부만 매핑한 경우도 매핑 안 한 컬럼을 DROP하려 든다.
- DB URL은 app.config(.env/환경변수)에서 읽는다(ini에 하드코딩 안 함).
"""
from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

from app.config import get_settings
from app.infra.db.session import Base

# 모델을 import해야 Base.metadata에 테이블이 등록된다.
# ⚠ 여기 빠뜨린 모델은 아래 화이트리스트에서 "관리 대상 아님"이 되어 조용히 제외된다.
#   (실제로 scrap이 빠져 company_bid_scraps가 관리 밖에 있었다.) 모델을 추가하면
#   반드시 여기도 함께 등록할 것.
import app.infra.db.models.bid  # noqa: F401
import app.infra.db.models.chat  # noqa: F401
import app.infra.db.models.company  # noqa: F401
import app.infra.db.models.company_profile  # noqa: F401
import app.infra.db.models.master  # noqa: F401
import app.infra.db.models.match_result  # noqa: F401
import app.infra.db.models.scrap  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 컬럼 일부만 매핑했거나(bid_table), 다른 팀이 소유해 coexist만 하는 테이블 —
# Base.metadata에 모델이 등록돼 있어도 관리 대상에서 뺀다.
_PARTIALLY_MAPPED_TABLES = {"bid_table"}

# 외부(에이전트팀/매칭 배치)가 공유 RDS에 직접 생성·적재하는 테이블(coexist).
# 우리 ORM은 읽기 매핑(컬럼 일부만)이라 관리 대상에 넣으면 autogenerate가
# 매핑 안 한 컬럼을 DROP하려 든다 — bid_table과 같은 이유로 시야에서 제외한다.
# (DDL 소유를 백엔드로 넘기기로 팀 합의가 되면 그때 마이그레이션으로 편입한다.)
_AGENT_OWNED_TABLES = {
    "company_qualifications", "company_regions", "company_licenses",
    "company_items", "company_certs", "company_personnel",
    "company_capacity_evals", "company_performance_records",
    "match_results",   # 배치(normalizer)가 사전계산 적재
    # 표준코드 마스터(에이전트팀 적재, 읽기 전용)
    "region_master", "license_master", "item_code_master",
    "personnel_grade_master", "cert_master",
}

_UNMANAGED_EVEN_IF_MAPPED = _PARTIALLY_MAPPED_TABLES | _AGENT_OWNED_TABLES


def _managed(table_name: str) -> bool:
    """이 레포가 마이그레이션으로 관리하는 테이블인가."""
    if table_name in _UNMANAGED_EVEN_IF_MAPPED:
        return False
    # 화이트리스트 — 모델로 매핑한 것만 관리한다. 목록을 손으로 유지하지 않으므로
    # DB에 새 테이블이 생겨도(다른 팀이 raw SQL로 추가) 자동으로 보호되고,
    # 반대로 모델을 추가하면 그 시점에 자동으로 관리 대상이 된다.
    return table_name in target_metadata.tables


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """모델로 매핑하지 않았거나, 매핑돼 있어도 외부 소유(coexist)인 테이블(과 그
    하위 객체)을 autogenerate/비교에서 제외."""
    if type_ == "table":
        return _managed(name)
    # 인덱스/제약 등 테이블에 딸린 객체도, 소속 테이블이 제외 대상이면 함께 제외.
    parent = getattr(obj, "table", None)
    if parent is not None:
        return _managed(parent.name)
    return True


def _url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
