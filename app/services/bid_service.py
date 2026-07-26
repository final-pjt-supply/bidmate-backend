# -*- coding: utf-8 -*-
"""공고 조회 유스케이스. 필터·정렬 정책과 페이지 계산, 그리고 플랫 ORM 행 →
중첩 응답 조립을 담당한다(쿼리 실행은 repository, HTTP 변환은 router).
"""
from datetime import datetime, timedelta, timezone

from app.api.v1.schemas.bid import BidDetail, BidListItem, BidListResponse
from app.api.v1.schemas.match_info import MatchInfo
from app.domain.enums import BidCategory, BidSortKey, SearchSortKey
from app.domain.qualification import Qualification
from app.infra.db.models.bid import Bid
from app.infra.db.repositories.bid_repository import BidRepository
from app.infra.db.repositories.match_repository import MatchRepository

PAGE_SIZE = 20   # 명세상 고정 — 홈·추천이 쓰는 GET /bids. 응답 계약(확정본) 값이라 바꾸지 않는다.
# 검색 화면 전용. 프론트 목록이 3열 그리드라 20이면 마지막 줄에 2개만 남는다.
# 24는 2·3·4·6열에 모두 나눠떨어져 열 수가 바뀌어도 어긋나지 않는다.
# GET /bids/search는 확정본 밖의 새 엔드포인트라 이 값은 계약 변경이 아니다.
# 화면마다 다른 크기가 필요해지면 page_size 파라미터(ge=1, le=100)를 연다.
SEARCH_PAGE_SIZE = 24
_KST = timezone(timedelta(hours=9))   # 한국 표준시(DST 없음). DB는 KST naive.


class BidNotFoundError(Exception):
    """존재하지 않거나 merged가 아닌 bid_id. 라우터가 404로 통일한다."""


class BidService:
    def __init__(self, repository: BidRepository, match_repository: MatchRepository | None = None):
        self._repo = repository
        # 선택 주입 — 상세(get_bid)에서만 쓰고 목록 경로들은 이 리포지토리가 필요 없다.
        self._match_repo = match_repository

    def list_bids(
        self,
        *,
        category: BidCategory | None,
        sort: BidSortKey,
        today: bool,
        page: int,
        company_id: str | None = None,
    ) -> BidListResponse:
        """목록.

        정렬 폴백: sort=score는 회사별 match_score 기준이지만 지금은 match_score가
        전부 null이라 deadline과 동일하게 처리한다. 회사별 점수 정렬이 실제로 붙으면
        company_id로 분기하는 자리를 아래에 표시해 둔다.
        """
        # TODO(company-scoped sort): sort=score이고 company_id가 있으면
        #   repo.list_page_by_score(company_id=...)로 회사별 점수순 정렬 경로를 탄다.
        #   지금은 score/deadline 모두 deadline(마감 임박순)으로 수렴.
        _ = sort  # 현재는 정렬 키가 결과를 바꾸지 않음(폴백). 시그니처는 계약상 유지.

        ntce_from, ntce_to = self._today_range() if today else (None, None)
        category_value = category.value if category is not None else None
        # 추천 페이지는 마감 지난 공고를 노출하지 않는다(결정 E, 2026-07-21 확정).
        # 마감 없는(NULL) 공고는 남긴다(repository._apply_filters 참조).
        now_kst = datetime.now(_KST).replace(tzinfo=None)

        total = self._repo.count(
            category=category_value,
            ntce_dt_from=ntce_from,
            ntce_dt_to=ntce_to,
            clse_after=now_kst,
        )
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_page(
            category=category_value,
            ntce_dt_from=ntce_from,
            ntce_dt_to=ntce_to,
            limit=PAGE_SIZE,
            offset=offset,
            clse_after=now_kst,
        )
        # 범위 밖 page는 rows=[]가 자연스럽게 나온다(200 + 빈 배열, 에러 아님).
        items = [BidListItem.model_validate(r) for r in rows]
        return BidListResponse(total=total, page=page, page_size=PAGE_SIZE, items=items)

    def search_bids(
        self,
        *,
        category: BidCategory | None,
        sort: SearchSortKey,
        page: int,
        q: str | None = None,
        include_closed: bool = False,
    ) -> BidListResponse:
        """공고 검색(GET /bids/search).

        list_bids와 분리된 이유는 repository의 search_* 주석 참조 — 홈·추천이 쓰는
        경로를 건드리지 않고 검색 필터를 계속 얹기 위해서다.

        list_bids와 달리 sort가 실제로 결과를 바꾼다.
        """
        category_value = category.value if category is not None else None
        sort_value = sort.value
        # 공백만 친 검색어는 "검색 안 함"으로 본다 — 안 그러면 '%   %' 패턴이 되어
        # 공백 없는 공고명이 통째로 빠진다.
        keyword = q.strip() if q else None
        keyword = keyword or None
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        # 기본은 마감 지난 공고를 제외한다(공용 경로와 동일 규칙). include_closed면
        # 필터를 걸지 않되, 정렬은 활성을 앞에 둔다(repository._deadline_order 참고) —
        # 안 그러면 이미 끝난 공고가 1페이지를 전부 차지한다.
        clse_after = None if include_closed else now_kst

        total = self._repo.search_count(category=category_value, clse_after=clse_after, q=keyword)
        offset = (page - 1) * SEARCH_PAGE_SIZE
        rows = self._repo.search_page(
            category=category_value,
            sort=sort_value,
            limit=SEARCH_PAGE_SIZE,
            offset=offset,
            now=now_kst,
            clse_after=clse_after,
            q=keyword,
        )
        items = [BidListItem.model_validate(r) for r in rows]
        return BidListResponse(total=total, page=page, page_size=SEARCH_PAGE_SIZE, items=items)

    def get_bid(self, bid_id: str, *, company_id: str | None = None) -> BidDetail:
        row = self._repo.get_by_bid_id(bid_id)
        if row is None:
            raise BidNotFoundError(bid_id)
        detail = BidDetail.model_validate(row)
        # 플랫 컬럼 → 중첩 qualification 조립(DB엔 별도 테이블/JSONB단일컬럼으로 없음).
        detail.qualification = Qualification.model_validate(row)
        # 비로그인이거나 아직 매칭이 계산되지 않았으면(프로필 미입력 등) null 그대로.
        # bid_id를 파싱해 ntce_no/ord를 뽑지 않는다(모델 주석: split 금지, 불투명 식별자) —
        # 이미 조회한 row에서 구조화된 필드를 그대로 쓴다.
        if self._match_repo is not None and company_id is not None:
            match_row = self._match_repo.get_one(
                int(company_id), row.bid_ntce_no, row.bid_ntce_ord
            )
            if match_row is not None:
                detail.match = MatchInfo.model_validate(match_row)
        return detail

    @staticmethod
    def _today_range() -> tuple[datetime, datetime]:
        """KST 오늘의 [00:00, 내일 00:00) naive 반개구간(bid_ntce_dt 비교용)."""
        now_kst = datetime.now(_KST).replace(tzinfo=None)
        start = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
