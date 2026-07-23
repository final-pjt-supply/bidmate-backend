# -*- coding: utf-8 -*-
"""user_events 쓰기(INSERT 전용).

append-only 테이블이라 조회/수정/삭제 메서드는 두지 않는다. 분석 질의는 별도 경로
(집계 API/분석 저장소)의 몫이다.
"""
from sqlalchemy.orm import Session

from app.infra.db.models.user_event import UserEvent


class EventRepository:
    def __init__(self, session: Session):
        self._session = session

    def insert(self, event: UserEvent) -> None:
        # 스케일 경로(의식적 결정): 지금(테스트/MVP)은 요청당 동기 INSERT.
        # 단일 행 INSERT는 마이크로초라 동시 수백 요청도 Postgres가 여유롭게 처리하고,
        # 전송 실패는 클라(fire-and-forget)가 흡수한다. 운영 트래픽이 커지면
        #   동기 → 클라 배칭(이벤트 묶음 전송) → SQS 완충 → S3/BigQuery 이관
        # 순으로 확장한다. Kafka/큐는 현 규모(8주 MVP)엔 오버라 도입하지 않는다.
        self._session.add(event)
        self._session.commit()
