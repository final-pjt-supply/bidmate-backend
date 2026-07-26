# -*- coding: utf-8 -*-
"""회사 x 공고 매칭 계산 — match_results 적재 (이슈 #57).

왜 스크립트인가:
  normalize 람다(realtime-normalize-dev)는 공고 요구사항 코드화(bid_require_*)까지만
  하고 회사 정보를 읽지 않는다. match_results를 채우는 주체가 없어 회원이 프로필을
  저장해도 매칭이 생기지 않았다. 그 빈자리를 메운다.

판정 구조(기존 9001 데이터에서 역추출):
  축(axis)마다 class가 있다 — gate(탈락) / supp(보완) / info(참고).
    gate : license, region, size, direct_prod
    supp : item, personnel, performance, capacity, credit
    info : cert                      (판정에 반영하지 않음)
  status는 충족 / 미충족 / 확인필요 3종.
    "확인필요"는 요구는 있는데 코드를 못 붙인 경우다(ETL 매핑 실패).
    미충족(=조건을 못 맞춤)과 구분해야 사용자에게 다르게 안내할 수 있다.

  요구가 없는 축은 아예 붙이지 않는다.

verdict:
    gate_failed >= 1                    -> 불가
    gate_failed = 0 and need_review >= 1 -> 확인필요
    gate 통과, supp 미충족               -> 보완가능
    전부 충족                            -> 가능
    축 0개                              -> 확인필요   <- 기존과 다른 점

  기존 로직은 축 0개를 "가능"으로 처리해 2,286건(가능의 31%)이 오탐이었다.
  축 0개는 "조건이 없다"가 아니라 "공고에서 조건을 못 뽑아냈다"이다.

사용법:
    python scripts/compute_matches.py --companies 1,4            # 실제 적재
    python scripts/compute_matches.py --companies 9001 --verify  # 임시 테이블에 쓰고 대조
    python scripts/compute_matches.py --companies 1 --dry-run    # 계산만
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date, datetime

import psycopg
from dotenv import load_dotenv

VERSION = "v2.0-rb"   # DB 컬럼이 varchar(10) — 넘기면 적재가 깨진다

GATE_AXES = {"license", "region", "size", "direct_prod"}
SUPP_AXES = {"item", "personnel", "performance", "capacity", "credit"}
INFO_AXES = {"cert"}

OK, NG, REVIEW = "충족", "미충족", "확인필요"

# 공고 규모제한 -> 통과하는 회원 규모 (HANDOFF 8-4, 실측 분포 기반)
SIZE_PASS = {
    "sme_only": {"small", "medium"},
    "small_only": {"small"},
    "no_conglomerate": {"small", "medium", "mid_large"},
    "no_large": {"small", "medium"},
    "none": {"small", "medium", "mid_large", "conglomerate"},
}


def _axis(name, cls, status, detail):
    return {"axis": name, "class": cls, "status": status, "detail": detail}


# ── 축별 판정 ────────────────────────────────────────────────────────

def axis_license(reqs, held: set):
    """OR 그룹 단위. 그룹 안의 코드 중 하나만 보유하면 그 그룹은 충족."""
    if not reqs:
        return None
    groups = defaultdict(list)
    for r in reqs:
        groups[r["or_group"]].append(r["license_code"])
    total = len(groups)
    satisfied = unresolved = 0
    for codes in groups.values():
        known = [c for c in codes if c]
        if not known:
            unresolved += 1          # 그룹 전체가 미매핑 -> 판정 불가
        elif held & set(known):
            satisfied += 1
    detail = f"{satisfied}/{total} 그룹 충족"
    if satisfied == total:
        return _axis("license", "gate", OK, detail)
    if unresolved:
        return _axis("license", "gate", REVIEW, detail)
    return _axis("license", "gate", NG, detail)


def axis_region(reqs, hq: str | None, branches: set, limit_type: str | None):
    """본점 소재지 제한(region_limit_type='hq_location')일 때만 게이트로 본다.

    bid_require_regions에 행이 있어도 그게 곧 참가 제한은 아니다 — 지역의무공동도급
    대상지역·현장 소재지 같은 표시용 정보가 섞여 있다. 실측상 기존 판정도
    hq_location인 공고에만 지역 축을 붙였다(그 외 518건은 축 없음).
    """
    if not reqs or limit_type != "hq_location":
        return None
    codes = {r["region_code"] for r in reqs if r["region_code"]}
    if not codes:
        return _axis("region", "gate", REVIEW, "요구 지역 불일치/미해석")
    if hq and hq in codes:
        return _axis("region", "gate", OK, "본점 소재지 충족")
    if branches & codes:
        return _axis("region", "gate", OK, "지사 소재지 충족")
    return _axis("region", "gate", NG, "요구 지역 불일치/미해석")


def axis_size(limit: str | None, size: str | None):
    if not limit:
        return None
    if not size:
        return _axis("size", "gate", REVIEW, f"{limit} vs (미입력)")
    allowed = SIZE_PASS.get(limit)
    detail = f"{limit} vs {size}"
    if allowed is None:
        return _axis("size", "gate", REVIEW, detail)
    return _axis("size", "gate", OK if size in allowed else NG, detail)


def axis_direct_prod(reqs, direct_items: set):
    """직생확인증명서를 요구하는 품목만 대상. 만료된 직생은 보유로 치지 않는다."""
    targets = [r for r in reqs if r["direct_production_req"]]
    if not targets:
        return None
    total = len(targets)
    codes = [r["item_code"] for r in targets]
    if not any(codes):
        return _axis("direct_prod", "gate", REVIEW, f"직생확인 0/{total}")
    have = sum(1 for c in codes if c and c in direct_items)
    detail = f"직생확인 {have}/{total}"
    if have == total:
        return _axis("direct_prod", "gate", OK, detail)
    if not all(codes):
        return _axis("direct_prod", "gate", REVIEW, detail)
    return _axis("direct_prod", "gate", NG, detail)


def axis_item(reqs, held: set):
    if not reqs:
        return None
    total = len(reqs)
    codes = [r["item_code"] for r in reqs]
    have = sum(1 for c in codes if c and c in held)
    detail = f"품목 등록 {have}/{total}"
    if have == total:
        return _axis("item", "supp", OK, detail)
    if not all(codes):
        return _axis("item", "supp", REVIEW, detail)
    return _axis("item", "supp", NG, detail)


def axis_personnel(reqs, staff: dict, grades: dict):
    """등급 요구는 '해당 rank 이상'으로 본다. 비교는 같은 field 안에서만 유효하다.
    (rank 있는 family는 역량등급/감리원/숙련기술자/학위 4개뿐 — HANDOFF 8-2)"""
    if not reqs:
        return None
    total = len(reqs)
    have = unresolved = 0
    for r in reqs:
        code = r["qual_code"]
        if not code:
            unresolved += 1
            continue
        need = r["headcount"] or 1
        g = grades.get(code)
        if g and g["rank"] is not None:
            # 같은 field에서 요구 rank 이상인 인력을 합산
            pool = sum(
                n for c, n in staff.items()
                if (h := grades.get(c)) and h["field"] == g["field"]
                and h["rank"] is not None and h["rank"] >= g["rank"]
            )
        else:
            pool = staff.get(code, 0)
        if pool >= need:
            have += 1
    detail = f"인력 요건 {have}/{total}"
    if have == total:
        return _axis("personnel", "supp", OK, detail)
    if unresolved:
        return _axis("personnel", "supp", REVIEW, detail)
    return _axis("personnel", "supp", NG, detail)


def axis_performance(reqs, records, today: date):
    """공고는 '최근 N년 / 분야 / 누계' 형태로 요구한다. 기간·분야로 거른 뒤 집계한다."""
    if not reqs:
        return None
    total = len(reqs)
    have = unresolved = 0
    for r in reqs:
        if r["parse_status"] and r["parse_status"] != "ok":
            unresolved += 1
            continue
        need = r["min_value"]
        if need is None:
            unresolved += 1
            continue
        years = r["period_years"]
        field = r["field_code"]
        pool = [
            rec for rec in records
            if (field is None or rec["field_code"] == field)
            and (years is None or rec["end_date"] is None
                 or (today - rec["end_date"]).days <= years * 366)
        ]
        if r["agg_type"] == "count":
            got = len(pool)
        elif r["agg_type"] == "single":
            got = max((rec["contract_amt"] or 0 for rec in pool), default=0)
        else:                                    # sum(기본) — 누계
            got = sum(rec["contract_amt"] or 0 for rec in pool)
        if got >= need:
            have += 1
    detail = f"실적 요건 {have}/{total}"
    if have == total:
        return _axis("performance", "supp", OK, detail)
    if unresolved:
        return _axis("performance", "supp", REVIEW, detail)
    return _axis("performance", "supp", NG, detail)


def axis_capacity(reqs, evals: dict):
    """업종별 시공능력평가액. license_code가 없으면 어느 업종인지 알 수 없어 판정 불가.
    (ETL 점검상 bid_require_capacity.license_code는 사실상 전부 NULL이다)"""
    if not reqs:
        return None
    total = len(reqs)
    have = unresolved = 0
    for r in reqs:
        code, need = r["license_code"], r["min_value"]
        if not code or need is None:
            unresolved += 1
            continue
        if evals.get(code, 0) >= need:
            have += 1
    detail = f"시공능력 {have}/{total}"
    if have == total:
        return _axis("capacity", "supp", OK, detail)
    if unresolved:
        return _axis("capacity", "supp", REVIEW, detail)
    return _axis("capacity", "supp", NG, detail)


def axis_credit(req, rating: str | None):
    """v1은 '신용평가등급 보유 여부'만 본다(공고가 등급 하한을 거는 사례가 실측 0건)."""
    if not req or not req.get("required"):
        return None
    if rating:
        return _axis("credit", "supp", OK, f"신용평가 보유({rating})")
    return _axis("credit", "supp", NG, "신용평가 미보유")


def axis_cert(reqs, held: set):
    """판정에 반영하지 않는다(class=info) — 인증 매핑률이 19.8%로 신뢰할 수 없다."""
    if not reqs:
        return None
    total = len(reqs)
    codes = [r["cert_code"] for r in reqs]
    have = sum(1 for c in codes if c and c in held)
    detail = f"인증 {have}/{total}"
    if have == total:
        return _axis("cert", "info", OK, detail)
    if not all(codes):
        return _axis("cert", "info", REVIEW, detail)
    return _axis("cert", "info", NG, detail)


# ── verdict ─────────────────────────────────────────────────────────

def compute_verdict(axes: list[dict]):
    """축 목록 -> (verdict, required, satisfied, gate_failed, need_review).
    info 축(cert)은 카운터에서 제외한다 — 판정에 쓰지 않으므로."""
    scored = [a for a in axes if a["class"] != "info"]
    required = len(scored)
    satisfied = sum(1 for a in scored if a["status"] == OK)
    gate_failed = sum(1 for a in scored if a["class"] == "gate" and a["status"] == NG)
    need_review = sum(1 for a in scored if a["status"] == REVIEW)

    if not scored:
        # 요구조건을 하나도 못 뽑아낸 공고 — "조건 없음"이 아니라 "모름"이다.
        return "확인필요", 0, 0, 0, 0
    if gate_failed:
        verdict = "불가"
    elif need_review:
        verdict = "확인필요"
    elif satisfied == required:
        verdict = "가능"
    else:
        verdict = "보완가능"
    return verdict, required, satisfied, gate_failed, need_review


# ── 데이터 로드 ──────────────────────────────────────────────────────

def load_company(cur, company_id: int) -> dict:
    def q(sql):
        cur.execute(sql, (company_id,))
        return cur.fetchall()

    licenses = {r[0] for r in q("select license_code from company_licenses where company_id=%s")}
    regions = q("select region_code, region_type from company_regions where company_id=%s")
    hq = next((r[0] for r in regions if r[1] == "hq"), None)
    branches = {r[0] for r in regions if r[1] != "hq"}
    items = q("select item_code, has_direct_production, direct_prod_valid_until from company_items where company_id=%s")
    today = date.today()
    certs = q("select cert_code, valid_until from company_certs where company_id=%s")
    cur.execute("select company_size, credit_rating from company_qualifications where company_id=%s", (company_id,))
    qual = cur.fetchone() or (None, None)

    return {
        "licenses": licenses,
        "hq": hq,
        "branches": branches,
        "items": {r[0] for r in items},
        # 만료된 직생은 매칭에서 빠진다
        "direct_items": {r[0] for r in items if r[1] and (r[2] is None or r[2] >= today)},
        "certs": {r[0] for r in certs if r[1] is None or r[1] >= today},
        "staff": dict(q("select qual_code, headcount from company_personnel where company_id=%s")),
        "evals": dict(q("select license_code, eval_amount from company_capacity_evals where company_id=%s")),
        "records": [
            {"field_code": r[0], "contract_amt": r[1], "end_date": r[2]}
            for r in q("select field_code, contract_amt, end_date from company_performance_records where company_id=%s")
        ],
        "size": qual[0],
        "credit": qual[1],
    }


def _group(cur, sql, cols):
    """(bid_ntce_no, bid_ntce_ord) -> [row dict] 로 묶어 메모리에 올린다."""
    cur.execute(sql)
    out = defaultdict(list)
    for row in cur.fetchall():
        out[(row[0], row[1])].append(dict(zip(cols, row[2:])))
    return out


def load_requirements(cur):
    return {
        "license": _group(cur, "select bid_ntce_no,bid_ntce_ord,or_group,license_code from bid_require_licenses", ("or_group", "license_code")),
        "region": _group(cur, "select bid_ntce_no,bid_ntce_ord,region_code from bid_require_regions", ("region_code",)),
        "item": _group(cur, "select bid_ntce_no,bid_ntce_ord,item_code,direct_production_req from bid_require_items", ("item_code", "direct_production_req")),
        "personnel": _group(cur, "select bid_ntce_no,bid_ntce_ord,qual_code,headcount from bid_require_personnel", ("qual_code", "headcount")),
        "performance": _group(cur, "select bid_ntce_no,bid_ntce_ord,min_value,agg_type,period_years,field_code,parse_status from bid_require_performances", ("min_value", "agg_type", "period_years", "field_code", "parse_status")),
        "capacity": _group(cur, "select bid_ntce_no,bid_ntce_ord,license_code,min_value from bid_require_capacity", ("license_code", "min_value")),
        "cert": _group(cur, "select bid_ntce_no,bid_ntce_ord,cert_code from bid_require_certs", ("cert_code",)),
    }


def load_grades(cur) -> dict:
    cur.execute("select qual_code, field, grade_rank from personnel_grade_master")
    return {r[0]: {"field": r[1], "rank": r[2]} for r in cur.fetchall()}


# ── 실행 ────────────────────────────────────────────────────────────

def compute_for_company(cur, company_id: int, bids, reqs, grades, sizes, limits) -> list[tuple]:
    co = load_company(cur, company_id)
    today = date.today()
    now = datetime.now()
    rows = []
    for no, ord_ in bids:
        key = (no, ord_)
        axes = [
            axis_license(reqs["license"].get(key, []), co["licenses"]),
            axis_region(reqs["region"].get(key, []), co["hq"], co["branches"], limits.get(key)),
            axis_size(sizes.get(key), co["size"]),
            axis_direct_prod(reqs["item"].get(key, []), co["direct_items"]),
            axis_item(reqs["item"].get(key, []), co["items"]),
            axis_personnel(reqs["personnel"].get(key, []), co["staff"], grades),
            axis_performance(reqs["performance"].get(key, []), co["records"], today),
            axis_capacity(reqs["capacity"].get(key, []), co["evals"]),
            axis_credit(reqs.get("credit", {}).get(key), co["credit"]),
            axis_cert(reqs["cert"].get(key, []), co["certs"]),
        ]
        axes = [a for a in axes if a]
        verdict, required, satisfied, gate_failed, need_review = compute_verdict(axes)
        rows.append((company_id, no, ord_, verdict, required, satisfied,
                     gate_failed, need_review, json.dumps(axes, ensure_ascii=False),
                     VERSION, now))
    return rows


def write_rows(conn, rows, company_id, table):
    with conn.cursor() as cur:
        cur.execute(f"delete from {table} where company_id=%s", (company_id,))
        cur.executemany(
            f"""insert into {table}
                (company_id, bid_ntce_no, bid_ntce_ord, verdict, required, satisfied,
                 gate_failed, need_review, axes, normalizer_version, computed_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            rows,
        )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", required=True, help="쉼표 구분 company_id")
    ap.add_argument("--verify", action="store_true", help="match_results_verify에 적재(원본 보존)")
    ap.add_argument("--dry-run", action="store_true", help="계산만 하고 쓰지 않음")
    args = ap.parse_args()

    load_dotenv(".env")
    conn = psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DBNAME"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode=os.getenv("POSTGRES_SSLMODE") or "require",
        connect_timeout=15,
    )
    table = "match_results_verify" if args.verify else "match_results"

    with conn.cursor() as cur:
        if args.verify:
            cur.execute("create table if not exists match_results_verify (like match_results including all)")
            conn.commit()
        cur.execute("select bid_ntce_no, bid_ntce_ord from bid_table where qual_status='merged'")
        bids = cur.fetchall()
        print(f"대상 공고 {len(bids):,}건")
        reqs = load_requirements(cur)
        cur.execute("select bid_ntce_no, bid_ntce_ord, size_limit from bid_require_size")
        sizes = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        cur.execute("select bid_ntce_no, bid_ntce_ord, region_limit_type from bid_table where qual_status='merged'")
        limits = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        cur.execute("select bid_ntce_no, bid_ntce_ord, required from bid_require_credit")
        reqs["credit"] = {(r[0], r[1]): {"required": r[2]} for r in cur.fetchall()}
        grades = load_grades(cur)

        for cid in [int(x) for x in args.companies.split(",")]:
            rows = compute_for_company(cur, cid, bids, reqs, grades, sizes, limits)
            dist = defaultdict(int)
            for r in rows:
                dist[r[3]] += 1
            print(f"\ncompany {cid}: {len(rows):,}행")
            for v, n in sorted(dist.items(), key=lambda x: -x[1]):
                print(f"   {v}: {n:,}")
            if args.dry_run:
                print("   (dry-run — 미적재)")
            else:
                write_rows(conn, rows, cid, table)
                print(f"   -> {table} 적재 완료")
    conn.close()


if __name__ == "__main__":
    main()
