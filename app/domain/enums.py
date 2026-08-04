# -*- coding: utf-8 -*-
"""도메인 열거형. bid_table의 코드성 컬럼값을 타입으로 고정한다.

CompanySizeLimit/RegionLimitType/AwardCutlineType는 추출 파이프라인
(pipeline/realtime/src/extractors/llm/schema.py의 _ENUM_FIELDS)이 dict 리터럴로
들고 있는 것과 동일한 값 집합이다 — "스키마 단일화"의 첫 접점. 지금은 값만 맞춰두고,
추후 파이프라인이 이 enum을 참조하도록 정리하면 중복이 사라진다.
"""
from enum import Enum


class BidCategory(str, Enum):
    """업무구분. bid_table.bid_category (NOT NULL, 수집 시 결정)."""
    CNSTWK = "cnstwk"   # 공사
    SERVC = "servc"     # 용역
    THNG = "thng"       # 물품
    FRGCPT = "frgcpt"   # 외자


class StatsCategory(str, Enum):
    """통계 화면의 업종 축. BidCategory + 업종 무관 'ALL'.

    ALL은 bid_table의 값이 아니라 집계 축이다(bid_stats matview가 c='ALL' 행을
    따로 갖는다). 업종별 상위 N을 합쳐서는 전체 순위가 나오지 않아 별도 집계가 필요하다.
    """
    ALL = "ALL"
    CNSTWK = "cnstwk"
    SERVC = "servc"
    THNG = "thng"
    FRGCPT = "frgcpt"


class QualStatus(str, Enum):
    """자격 병합 상태. DB enum이 아니라 VARCHAR + 애플리케이션 규약
    (merge/logic.py determine_qual_status가 SSOT). 조회 API는 merged만 노출한다."""
    PENDING = "pending"
    MERGED = "merged"
    PARTIAL = "partial"
    FAILED = "failed"


class CompanySizeLimit(str, Enum):
    SME_ONLY = "sme_only"
    SMALL_ONLY = "small_only"
    NO_LARGE = "no_large"
    NO_CONGLOMERATE = "no_conglomerate"
    NONE = "none"


class RegionLimitType(str, Enum):
    HQ_LOCATION = "hq_location"
    NONE = "none"


class AwardCutlineType(str, Enum):
    SCORE = "score"
    RATE = "rate"
    LOWEST_PRICE = "lowest_price"   # 이 경우 award_cutline_value=null이 정상


class BidSortKey(str, Enum):
    """목록 정렬 키.

    score는 회사별 match_score 기준이지만 현 단계에선 match_score가 전부 null이라
    deadline으로 폴백한다(services.bid_service 참조). 값 집합 자체는 프론트 계약이라
    지금 확정해둔다.
    """
    SCORE = "score"
    DEADLINE = "deadline"


class SearchSortKey(str, Enum):
    """검색 화면(GET /bids/search) 정렬 키.

    BidSortKey와 분리한 이유: 검색은 deadline·recent 둘만 노출하고 score는 받지
    않는다(match_score가 전부 null이라 의미가 없다). 공용 BidSortKey에 recent를
    더하면 홈·추천이 쓰는 GET /bids에서도 recent가 통과해버리므로, 계약을 섞지 않는다.
    """
    DEADLINE = "deadline"   # 마감 임박순(bid_clse_dt ASC)
    RECENT = "recent"       # 최신 등록순(bid_ntce_dt DESC)


class MatchSortKey(str, Enum):
    """맞춤추천(GET /me/matches) 정렬 키.

    SearchSortKey와 분리한 이유: recommended는 회사별 사전계산 결과(match_results)의
    충족 비율에 기대는데, /bids·/bids/search는 공용 공고라 그 값이 없다. 공용 키에
    섞으면 검색에서도 recommended가 통과해버리므로 계약을 분리한다.
    """
    DEADLINE = "deadline"        # 마감 임박순(bid_clse_dt ASC)
    RECENT = "recent"            # 최신 등록순(bid_ntce_dt DESC)
    RECOMMENDED = "recommended"  # 추천순 — 자격 충족 비율(satisfied/required) DESC
