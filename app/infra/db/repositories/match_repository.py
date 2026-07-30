# -*- coding: utf-8 -*-
"""회사별 매칭 조회 — match_results ⋈ bid_table.

노출 게이트(qual_status='merged')를 여기서 강제한다(bid_repository와 같은 불변식).
마감 지난 공고는 제외한다(추천/홈과 동일 규칙 — '지금 넣을 수 있는' 매칭만).
참가 불가 판정도 기본 제외한다(추천 목록이 불가로 채워지지 않게).
계산은 안 한다 — match_results는 배치가 채운 사전계산 결과다.
"""
from datetime import datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid
from app.infra.db.models.match_result import MatchResult

_MERGED = QualStatus.MERGED.value
_INFEASIBLE = "불가"

# match_results ⋈ bid_table 조인 조건 — count/list_page가 공유한다.
# (따로 쓰면 한쪽만 고쳐져 total과 목록이 어긋난다.)
_JOIN_ON = (Bid.bid_ntce_no == MatchResult.bid_ntce_no) & (
    Bid.bid_ntce_ord == MatchResult.bid_ntce_ord
)


class MatchRepository:
    def __init__(self, session: Session):
        self._session = session

    @staticmethod
    def _apply_filters(
        stmt, company_id: int, clse_after: datetime, include_infeasible: bool
    ):
        stmt = stmt.where(
            Bid.qual_status == _MERGED,
            MatchResult.company_id == company_id,
            # 마감 지난 공고 제외. 마감일 NULL은 "아직 안 닫힘"으로 보고 남긴다(공용 규칙).
            or_(Bid.bid_clse_dt >= clse_after, Bid.bid_clse_dt.is_(None)),
        )
        if not include_infeasible:
            # verdict NULL(판정 없음)은 남긴다 — 불가라고 단정할 근거가 없다.
            stmt = stmt.where(
                or_(MatchResult.verdict != _INFEASIBLE, MatchResult.verdict.is_(None))
            )
        return stmt

    def get_one(
        self, company_id: int, bid_ntce_no: str, bid_ntce_ord: str
    ) -> MatchResult | None:
        """공고 상세용 단건 조회. PK 조회라 merged·마감 필터가 필요 없다
        (그 판단은 이미 상세 화면에 도달했다는 것 자체가 의미한다 — BidService가
        get_by_bid_id에서 merged를 이미 검증한다)."""
        stmt = select(MatchResult).where(
            MatchResult.company_id == company_id,
            MatchResult.bid_ntce_no == bid_ntce_no,
            MatchResult.bid_ntce_ord == bid_ntce_ord,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def count(
        self, company_id: int, *, clse_after: datetime, include_infeasible: bool = False
    ) -> int:
        stmt = select(func.count()).select_from(MatchResult).join(Bid, _JOIN_ON)
        stmt = self._apply_filters(stmt, company_id, clse_after, include_infeasible)
        return self._session.execute(stmt).scalar_one()

    def list_page(
        self,
        company_id: int,
        *,
        sort: str,
        limit: int,
        offset: int,
        clse_after: datetime,
        include_infeasible: bool = False,
    ) -> list[tuple[MatchResult, Bid]]:
        """한 페이지의 (매칭, 공고) 쌍.

        마감 전만 노출하므로 정렬은 단순하다(활성/마감 버킷 불필요):
          * deadline   : 마감 임박순 bid_clse_dt ASC + NULLS LAST
          * recent     : 최신 등록순 bid_ntce_dt DESC + NULLS LAST
          * recommended: 자격 충족 비율(satisfied/required) DESC — 많이 충족한 공고부터.
                         required=0(확인필요)은 비율이 없어 NULLS LAST로 맨 뒤.
        페이지 경계에서 행이 중복·누락되지 않게 마감임박+bid_id로 tie-break.
        """
        stmt = select(MatchResult, Bid).join(Bid, _JOIN_ON)
        stmt = self._apply_filters(stmt, company_id, clse_after, include_infeasible)
        if sort == "recent":
            order = (Bid.bid_ntce_dt.desc().nulls_last(), Bid.bid_id.asc())
        elif sort == "recommended":
            # required=0이면 0으로 나누게 되므로 NULLIF로 NULL 처리(→ NULLS LAST).
            ratio = MatchResult.satisfied * 1.0 / func.nullif(MatchResult.required, 0)
            order = (
                ratio.desc().nulls_last(),
                Bid.bid_clse_dt.asc().nulls_last(),   # 동률이면 마감 임박 우선
                Bid.bid_id.asc(),
            )
        else:
            order = (Bid.bid_clse_dt.asc().nulls_last(), Bid.bid_id.asc())
        stmt = stmt.order_by(*order).limit(limit).offset(offset)
        return [(row[0], row[1]) for row in self._session.execute(stmt).all()]

    # compute_match_results의 RETURNS TABLE 10컬럼을 명시로 뽑는다. SELECT * 로 받으면
    # 함수 출력 순서/개수가 바뀔 때 INSERT 컬럼과 조용히 어긋나 오적재된다 — 명시하면
    # 불일치 시 곧바로 에러가 난다(계약 결합 하드닝). computed_at은 기본값(now KST)이라 뺀다.
    _COMPUTE_COLS = (
        "company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied, "
        "gate_failed, need_review, axes, normalizer_version"
    )
    _RESEED_SQL = text(
        "INSERT INTO match_results "
        f"({_COMPUTE_COLS}) "
        f"SELECT {_COMPUTE_COLS} FROM compute_match_results(CAST(:cid AS bigint))"
    )
    _DELETE_SQL = text("DELETE FROM match_results WHERE company_id = :cid")

    def recompute_for_company(self, company_id: int) -> int:
        """그 회사 매칭을 전체 교체(멱등) — 자격/공고 변경 후 신선도 유지용.

        compute_match_results(company_id) DB 함수 결과로 match_results를 다시
        채운다. DELETE+INSERT를 한 트랜잭션으로 커밋(원자적). 실패 시 롤백 후
        재던진다 — 호출자(라우터)가 저장 자체는 성공시키고 로그만 남긴다.
        일배치(공고 신선도)와 겹쳐도 회사 단위 full replace라 last-writer-wins.
        """
        s = self._session
        try:
            s.execute(self._DELETE_SQL, {"cid": company_id})
            s.execute(self._RESEED_SQL, {"cid": company_id})
            s.commit()
        except Exception:
            s.rollback()
            raise
        return s.execute(
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.company_id == company_id)
        ).scalar_one()

    # ── 주기 갱신(#80) ────────────────────────────────────────
    # 증분: 새로 정규화된 공고 행만 UPSERT. 전체 교체를 5분마다 하면 회사 100곳
    # 기준 14만 행 × 288회/일이 dead tuple·WAL로 쌓여 RDS가 그 청소에 매달린다.
    # 실제로 5분 사이 바뀌는 건 새로 들어온 공고 수십 건뿐이라 그것만 쓴다.
    #
    # computed_at을 UPDATE에서 명시적으로 갱신한다 — 기본값은 INSERT에만 적용되고,
    # 이 값이 다음 주기의 '어디까지 계산했나' 기준이라 안 올리면 같은 공고를 계속 다시 쓴다.
    # 별칭 m으로 뽑으므로 컬럼도 m. 접두로 명시한다(B-1과 같은 이유 — SELECT * 금지).
    _COMPUTE_COLS_M = ", ".join(f"m.{c.strip()}" for c in _COMPUTE_COLS.split(","))
    _UPSERT_SINCE_SQL = text(
        "INSERT INTO match_results "
        f"({_COMPUTE_COLS}) "
        f"SELECT {_COMPUTE_COLS_M} FROM compute_match_results(CAST(:cid AS bigint)) m "
        "WHERE (m.bid_ntce_no, m.bid_ntce_ord) IN ("
        "  SELECT s.bid_ntce_no, s.bid_ntce_ord FROM bid_require_summary s"
        "  WHERE s.normalized_at > :since) "
        "ON CONFLICT (company_id, bid_ntce_no, bid_ntce_ord) DO UPDATE SET "
        "  verdict = EXCLUDED.verdict, required = EXCLUDED.required, "
        "  satisfied = EXCLUDED.satisfied, gate_failed = EXCLUDED.gate_failed, "
        "  need_review = EXCLUDED.need_review, axes = EXCLUDED.axes, "
        "  normalizer_version = EXCLUDED.normalizer_version, "
        "  computed_at = (now() AT TIME ZONE 'Asia/Seoul')"
    )

    def latest_normalized_at(self) -> datetime | None:
        """자격요건 정규화가 마지막으로 진행된 시각(normalize 람다가 찍는다)."""
        return self._session.execute(
            text("SELECT max(normalized_at) FROM bid_require_summary")
        ).scalar_one()

    def latest_computed_at(self) -> datetime | None:
        """매칭이 마지막으로 계산된 시각. 증분 갱신의 하한선."""
        return self._session.execute(
            select(func.max(MatchResult.computed_at))
        ).scalar_one()

    def active_company_ids(self) -> list[int]:
        """탈퇴하지 않은 회사 id. 탈퇴 회사는 계산해봐야 볼 사람이 없다."""
        rows = self._session.execute(
            text("SELECT id FROM companies WHERE deleted_at IS NULL ORDER BY id")
        ).scalars().all()
        return list(rows)

    def upsert_since(self, company_id: int, since: datetime) -> int:
        """since 이후 정규화된 공고에 대해서만 그 회사 매칭을 갱신한다.

        마감이 지나 계산 대상에서 빠진 공고 행은 여기서 지워지지 않는다 —
        조회가 마감으로 거르므로 화면엔 영향이 없고, 정리는 야간 전체 재계산이
        맡는다(증분에 DELETE를 섞으면 전체 스캔이라 증분의 이점이 사라진다).
        반환: 이번에 쓴 행 수.
        """
        s = self._session
        try:
            result = s.execute(
                self._UPSERT_SINCE_SQL, {"cid": company_id, "since": since}
            )
            s.commit()
        except Exception:
            s.rollback()
            raise
        return result.rowcount or 0
