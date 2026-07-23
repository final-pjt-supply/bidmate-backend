# -*- coding: utf-8 -*-
"""고객 여정 이벤트 카탈로그 (v1, 프론트 확정 15종).

이벤트 이름/유형을 코드 상수로 고정한다(자유 문자열 금지 — home_view/view_home/
HomeViewed 난립 방지). event_type은 프론트가 보내지 않고 event_name에서 서버가
파생한다(둘이 어긋나지 않게 단일 소스로 관리). 새 이벤트는 여기 + 매핑에 등록해야
수집된다(모르는 이름은 422). DB 테이블(user_events)의 컬럼은 이 목록과 무관하게
동일하다 — event_name은 varchar이고, 여기 enum은 애플리케이션 검증용이다.
"""
from enum import Enum


class EventType(str, Enum):
    """이벤트 대분류(집계용). *_viewed=page_view, 링크/카드 클릭=click,
    상태 변경(제출/저장/전송/스크랩/검색/로그인)=action."""
    PAGE_VIEW = "page_view"
    CLICK = "click"
    ACTION = "action"


class EventName(str, Enum):
    """수집 허용 이벤트(v1 15종). {대상}_{동사} 소문자 스네이크."""
    # ① 진입 / 세션
    HOME_VIEWED = "home_viewed"
    MYPAGE_VIEWED = "mypage_viewed"
    LOGIN_COMPLETED = "login_completed"
    # ② 탐색 / 필터
    BID_LIST_FILTERED = "bid_list_filtered"
    BID_CARD_CLICKED = "bid_card_clicked"
    BID_DETAIL_VIEWED = "bid_detail_viewed"
    # ③ 강한 관심 신호 (추천·전환 핵심)
    BID_QUESTION_CLICKED = "bid_question_clicked"
    BID_EXTERNAL_LINK_CLICKED = "bid_external_link_clicked"
    BID_BOOKMARKED = "bid_bookmarked"          # properties.on 으로 on/off
    SEARCH_SUBMITTED = "search_submitted"      # properties.query_len 만(원문 X)
    # ④ 챗봇
    CHATBOT_OPENED = "chatbot_opened"
    CHATBOT_MESSAGE_SENT = "chatbot_message_sent"
    # ⑤ 전환 / 가입
    SIGNUP_COMPLETED = "signup_completed"
    PROFILE_UPDATED = "profile_updated"
    COMPANY_PROFILE_SUBMITTED = "company_profile_submitted"


# event_name → event_type 파생 매핑(SSOT).
EVENT_TYPE_BY_NAME: dict[EventName, EventType] = {
    # ① 진입 / 세션
    EventName.HOME_VIEWED: EventType.PAGE_VIEW,
    EventName.MYPAGE_VIEWED: EventType.PAGE_VIEW,
    EventName.LOGIN_COMPLETED: EventType.ACTION,
    # ② 탐색 / 필터
    EventName.BID_LIST_FILTERED: EventType.ACTION,
    EventName.BID_CARD_CLICKED: EventType.CLICK,
    EventName.BID_DETAIL_VIEWED: EventType.PAGE_VIEW,
    # ③ 강한 관심 신호
    EventName.BID_QUESTION_CLICKED: EventType.CLICK,
    EventName.BID_EXTERNAL_LINK_CLICKED: EventType.CLICK,
    EventName.BID_BOOKMARKED: EventType.ACTION,
    EventName.SEARCH_SUBMITTED: EventType.ACTION,
    # ④ 챗봇
    EventName.CHATBOT_OPENED: EventType.CLICK,
    EventName.CHATBOT_MESSAGE_SENT: EventType.ACTION,
    # ⑤ 전환 / 가입
    EventName.SIGNUP_COMPLETED: EventType.ACTION,
    EventName.PROFILE_UPDATED: EventType.ACTION,
    EventName.COMPANY_PROFILE_SUBMITTED: EventType.ACTION,
}
