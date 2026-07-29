# -*- coding: utf-8 -*-
"""추천 로직 검증용 테스트 회사 시딩 (이슈 #56).

왜 필요한가:
  실회원 3명 중 계약 실적이 입력된 회사가 없다. 그대로 추천을 돌리면 쿼리 사다리가
  항상 맨 아래(면허명)까지 폴백돼, 정작 검증하려는 "실적 기반 추천"이 한 번도
  실행되지 않는다.

검증 설계 — A와 B는 면허가 같고 실적만 다르다:
  A: 정보통신공사업 + CCTV·영상감시 계열 실적
  B: 정보통신공사업 + 네트워크·전산망 계열 실적   ← A와 동일 면허
  C: 정보통신공사업 + 실적 0건, 품목만            ← 콜드스타트 폴백 검증

  A와 B의 추천 상위가 비슷하게 나오면 이 기능은 실패다 — 면허만 보는 기존 매칭과
  다를 게 없다는 뜻이다. 후보 규모: 인덱스 내 merged 공고 중 CCTV 29건 / 네트워크 94건.

실적명은 나라장터 실제 공고 제목을 쓴다(2026-07-24 등록분, 우리 인덱스에 미수집
확인됨). 지어낸 제목("CCTV 공사 3건")은 실제 공고 문체와 달라 BGE-M3 유사도가
왜곡되고, 인덱스에 있는 제목을 쓰면 자기 자신과 매칭돼 검증이 무의미해진다.

사용법:
    python scripts/seed_test_companies.py            # 시딩
    python scripts/seed_test_companies.py --cleanup   # 삭제(운영 DB 정리)
    python scripts/seed_test_companies.py --show      # 현황만 조회
"""
from __future__ import annotations

import argparse
import os
from datetime import date

import psycopg
from dotenv import load_dotenv

# [TEST] 접두어 — 운영 DB에 들어가므로 나중에 골라 지울 수 있게 표시한다.
PREFIX = "[TEST]"

LICENSE_ICT = ("0036", "정보통신공사업")

# 나라장터 2026-07-24 등록분 실제 공고 제목. 우리 인덱스(19,317건)에 없는 것만 골랐다.
CCTV_RECORDS = [
    "cctv시스템 추가공사",
    "교통정보센터 노후장비 구매설치(VMS, CCTV) 감리용역",
    "2026년 신천대로 지하차도 진입차단시설 CCTV 설치공사",
]
NETWORK_RECORDS = [
    "`26년도 네트워크 보안장비 개선",
    "2026년 재난안전통신망 지령장치 및 네트워크 장비 유지보수 용역",
    "[글로컬] 네트워크 고도화 스위치 구매",
]

# 품목 코드는 실제 item_code_master 값이어야 FK/검증을 통과한다(10자리 세부품명).
# C회사 콜드스타트용 — 영상감시 계열.
ITEM_CODES_C = ["4617162201"]   # 영상감시장치 (실제 item_code_master 확인값)

TEST_COMPANIES = [
    {
        "key": "A",
        "name": f"{PREFIX}에이통신",
        "email": "test-a@bidmate.test",
        "records": CCTV_RECORDS,
        "items": [],
    },
    {
        "key": "B",
        "name": f"{PREFIX}비네트워크",
        "email": "test-b@bidmate.test",
        "records": NETWORK_RECORDS,
        "items": [],
    },
    {
        "key": "C",
        "name": f"{PREFIX}씨콜드스타트",
        "email": "test-c@bidmate.test",
        "records": [],           # 실적 0건 — 폴백 경로 검증
        "items": ITEM_CODES_C,
    },
]

# 실적 금액대는 후보 공고 규모와 겹치게 둔다(1~5억). 너무 작으면 나중에 금액 규칙을
# 붙일 때 전부 걸러지고, 너무 크면 반대가 된다.
AMOUNTS = [180_000_000, 320_000_000, 450_000_000]
END_DATES = [date(2025, 11, 30), date(2025, 6, 15), date(2024, 9, 30)]


def _conn():
    load_dotenv(".env")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DBNAME"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode=os.getenv("POSTGRES_SSLMODE") or "require",
        connect_timeout=15,
    )


# company_* 자식 테이블 — cleanup에서 회사 스코프로 지운다.
CHILD_TABLES = [
    "company_qualifications", "company_regions", "company_licenses",
    "company_items", "company_certs", "company_personnel",
    "company_capacity_evals", "company_performance_records",
    "match_results",
]


def show(cur) -> list[tuple[int, str]]:
    cur.execute(
        "select id, name, email from companies where name like %s order by id",
        (f"{PREFIX}%",),
    )
    rows = cur.fetchall()
    if not rows:
        print("테스트 회사 없음")
        return []
    print("=== 테스트 회사 ===")
    for cid, name, email in rows:
        counts = []
        for t in ("company_licenses", "company_performance_records", "company_items", "match_results"):
            cur.execute(f"select count(*) from {t} where company_id=%s", (cid,))
            counts.append(f"{t.replace('company_','').replace('_records','')}={cur.fetchone()[0]}")
        print(f"  id={cid} {name} ({email})  " + " ".join(counts))
    return [(r[0], r[1]) for r in rows]


def cleanup(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("select id from companies where name like %s", (f"{PREFIX}%",))
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            print("지울 테스트 회사가 없습니다.")
            return
        for t in CHILD_TABLES:
            cur.execute(f"delete from {t} where company_id = any(%s)", (ids,))
        cur.execute("delete from companies where id = any(%s)", (ids,))
    conn.commit()
    print(f"삭제 완료: company_id {ids}")


def seed(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("select count(*) from companies where name like %s", (f"{PREFIX}%",))
        if cur.fetchone()[0]:
            print("이미 테스트 회사가 있습니다. --cleanup 후 다시 실행하세요.")
            return

        # 마스터에서 이름을 가져와 비정규화 컬럼을 채운다(서버가 채우는 규약과 동일).
        cur.execute("select license_name from license_master where license_code=%s", (LICENSE_ICT[0],))
        row = cur.fetchone()
        license_name = row[0] if row else LICENSE_ICT[1]

        for spec in TEST_COMPANIES:
            # cognito_sub은 NOT NULL일 수 있어 테스트용 고유값을 넣는다(로그인 불가 값).
            cur.execute(
                """insert into companies (cognito_sub, email, name)
                   values (%s, %s, %s) returning id""",
                (f"test-sub-{spec['key'].lower()}", spec["email"], spec["name"]),
            )
            cid = cur.fetchone()[0]

            cur.execute(
                """insert into company_qualifications (company_id, company_size, credit_rating)
                   values (%s, %s, %s)""",
                (cid, "medium", "A"),
            )
            cur.execute(
                """insert into company_regions (company_id, region_code, region_name, region_type)
                   values (%s, %s, %s, %s)""",
                (cid, "11", "서울특별시", "hq"),
            )
            cur.execute(
                """insert into company_licenses (company_id, license_code, license_name)
                   values (%s, %s, %s)""",
                (cid, LICENSE_ICT[0], license_name),
            )

            for i, title in enumerate(spec["records"]):
                cur.execute(
                    """insert into company_performance_records
                       (company_id, contract_name, field_code, field_name, contract_amt, end_date)
                       values (%s, %s, %s, %s, %s, %s)""",
                    (cid, title, LICENSE_ICT[0], license_name,
                     AMOUNTS[i % len(AMOUNTS)], END_DATES[i % len(END_DATES)]),
                )

            for code in spec["items"]:
                cur.execute("select item_name from item_code_master where item_code=%s", (code,))
                r = cur.fetchone()
                if r is None:
                    print(f"  ⚠ 품목코드 {code}가 마스터에 없어 건너뜀")
                    continue
                cur.execute(
                    """insert into company_items
                       (company_id, item_code, item_name, has_direct_production, direct_prod_valid_until)
                       values (%s, %s, %s, %s, %s)""",
                    (cid, code, r[0], True, date(2027, 12, 31)),
                )

            print(f"  {spec['name']} → company_id={cid}, 실적 {len(spec['records'])}건, 품목 {len(spec['items'])}건")
    conn.commit()
    print("\n시딩 완료. 매칭은 compute_match_results(DB 함수) 재적재 또는 "
          "프로필 저장 훅(PUT /me/profile)으로 계산된다.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true", help="테스트 회사 전부 삭제")
    ap.add_argument("--show", action="store_true", help="현황만 조회")
    args = ap.parse_args()

    conn = _conn()
    try:
        if args.cleanup:
            cleanup(conn)
        elif args.show:
            with conn.cursor() as cur:
                show(cur)
        else:
            seed(conn)
            with conn.cursor() as cur:
                show(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
