# -*- coding: utf-8 -*-
"""companies 조회/생성.

Cognito에 가입한 사용자는 우리 DB에 자동으로 생기지 않는다. 그래서 인증된 첫 요청에
JIT(Just-In-Time)로 회사 행을 만든다 — 별도 '가입 완료' API 없이도 company_id가
확보되고, 이후 회사정보(company_* 테이블)는 이 id에 붙는다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db.models.company import Company


class CompanyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, company_id: str | int) -> Company | None:
        return self._session.get(Company, int(company_id))

    def get_by_cognito_sub(self, cognito_sub: str) -> Company | None:
        stmt = select(Company).where(Company.cognito_sub == cognito_sub)
        return self._session.scalars(stmt).first()

    def get_by_email(self, email: str) -> Company | None:
        stmt = select(Company).where(Company.email == email)
        return self._session.scalars(stmt).first()

    def get_or_create(self, *, cognito_sub: str, email: str | None, name: str) -> Company:
        """cognito_sub로 찾고, 없으면 만든다(JIT).

        같은 이메일로 먼저 만들어진 행(예: 팀이 수동 생성)이 있으면 새로 만들지 않고
        그 행에 cognito_sub를 연결한다 — 회사가 중복 생성되는 걸 막는다.
        """
        company = self.get_by_cognito_sub(cognito_sub)
        if company is not None:
            return company

        if email:
            existing = self.get_by_email(email)
            if existing is not None:
                existing.cognito_sub = cognito_sub  # 기존 회사에 계정 연결
                self._session.commit()
                self._session.refresh(existing)
                return existing

        company = Company(cognito_sub=cognito_sub, email=email, name=name)
        self._session.add(company)
        self._session.commit()
        self._session.refresh(company)
        return company
