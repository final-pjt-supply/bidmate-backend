# -*- coding: utf-8 -*-
"""추천 로직 검증 스크립트 — 버리는 코드 (이슈 #56).

프로덕션 코드가 아니다. 컷라인을 사람 눈으로 정하고 쿼리 구성을 반복 수정하기 위한
실험 도구다. 검증이 끝나면 이 로직을 app/recommender/로 옮긴다.

파이프라인:
  1. 쿼리 사다리 — 스크랩/원문클릭 공고 제목 → 실적 계약명 → 품목명 → 면허명
     (여러 개를 하나로 평균하지 않는다. 건별로 각각 knn을 던지고 나중에 병합한다.
      CCTV 실적과 네트워크 실적을 평균하면 둘 다 아닌 중간점이 나온다.)
  2. Cloudflare BGE-M3로 쿼리 임베딩 (인덱스와 같은 모델·1024차원)
  3. 후보 제목 유사도로 상위 N건 shortlist 생성
  4. OpenSearch knn — shortlist의 기본 text 청크만 (table은 단가표·산출내역이라 어느 공고나
     비슷해 무관한 공고 점수를 올린다. --include-tables로 비교 가능)
  5. 본문 점수 = 그 공고에서 잡힌 청크 상위 3개 평균
     (max는 청크 많은 공고가 유리해 길이 편향, 전체 평균은 정형 문구가 지배)
  6. 쿼리 간 병합 = 최고 점수 채택, 최종 점수 = 제목 0.8 + 본문 0.2
  7. 후보 제한 — match_results에서 verdict가 불가가 아닌 것, 마감 전, 스크랩 제외
  8. 상위 N개를 마크다운 표로 출력. 컷은 적용하지 않는다 — 점수 분포를 봐야
     컷라인을 정할 수 있다.

사용법:
    python scripts/try_recommend.py --company 11
    python scripts/try_recommend.py --company 11 --include-tables   # 비교용
    python scripts/try_recommend.py --company 11 --top 30
"""
from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict

import psycopg
import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings()

CHUNKS_PER_QUERY = 3000  # 쿼리당 knn으로 가져올 청크 수. 후보 공고가 수백 건이고
                         # 공고당 청크가 30~114개라, 이보다 작으면 후보 대부분이 안 잡힌다.
TOP_CHUNKS_PER_BID = 3   # 공고 점수 = 상위 N개 청크 평균
EMBED_BATCH = 100
DEFAULT_TITLE_WEIGHT = 0.8
DEFAULT_SHORTLIST = 100


def _db():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5433")),
        dbname=os.getenv("POSTGRES_DB") or os.getenv("POSTGRES_DBNAME"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        sslmode=os.getenv("POSTGRES_SSLMODE") or "require",
        connect_timeout=15,
    )


# ── 1) 쿼리 사다리 ──────────────────────────────────────────────────

def build_queries(cur, company_id: int) -> tuple[list[str], str]:
    """(쿼리 목록, 어느 단계에서 나왔는지). 위 단계에서 하나라도 나오면 거기서 멈춘다."""
    # 스크랩한 공고 제목 (원문클릭은 S3에만 있어 이번 검증에선 제외 — #61 수정 이후
    # 쌓이는 데이터가 필요하다)
    cur.execute(
        """select b.bid_ntce_nm from company_bid_scraps s
           join bid_table b on b.bid_ntce_no=s.bid_ntce_no and b.bid_ntce_ord=s.bid_ntce_ord
           where s.company_id=%s and b.bid_ntce_nm is not null""",
        (company_id,),
    )
    scraps = [r[0] for r in cur.fetchall()]
    if scraps:
        return scraps, "스크랩 공고 제목"

    # 실적 계약명 — 최근 완료일 우선 10건
    cur.execute(
        """select contract_name from company_performance_records
           where company_id=%s and contract_name is not null
           order by end_date desc nulls last limit 10""",
        (company_id,),
    )
    perfs = [r[0] for r in cur.fetchall()]
    if perfs:
        return perfs, "실적 계약명"

    # 품목명
    cur.execute(
        "select item_name from company_items where company_id=%s and item_name is not null",
        (company_id,),
    )
    items = [r[0] for r in cur.fetchall()]
    if items:
        return items, "취급 품목명"

    # 면허명 — 최후 폴백. 512건 어디에나 해당돼 변별력이 거의 없다.
    cur.execute(
        "select license_name from company_licenses where company_id=%s and license_name is not null",
        (company_id,),
    )
    return [r[0] for r in cur.fetchall()], "면허명(변별력 낮음)"


# ── 2) 임베딩 ───────────────────────────────────────────────────────

def embed(texts: list[str]) -> list[list[float]]:
    acct = os.environ["CF_ACCOUNT_ID"]
    token = os.environ["CF_API_TOKEN"]
    model = os.environ.get("CF_EMBEDDING_MODEL", "@cf/baai/bge-m3")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"text": batch},
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            raise RuntimeError(f"임베딩 실패: {str(d)[:300]}")
        out.extend(d["result"]["data"])
    return out


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"벡터 차원 불일치: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def title_scores(
    queries: list[str],
    query_vectors: list[list[float]],
    titles: dict[str, str],
    title_vectors: list[list[float]],
) -> tuple[dict[str, float], dict[str, str]]:
    """공고별 최고 제목 유사도와 그 점수를 만든 쿼리를 반환한다."""
    scores: dict[str, float] = {}
    hit_by: dict[str, str] = {}
    for (bid_id, _), title_vector in zip(titles.items(), title_vectors):
        for query, query_vector in zip(queries, query_vectors):
            score = cosine(query_vector, title_vector)
            if bid_id not in scores or score > scores[bid_id]:
                scores[bid_id] = score
                hit_by[bid_id] = query
    return scores, hit_by


def hybrid_score(title_score: float, body_score: float, title_weight: float) -> float:
    return title_weight * title_score + (1.0 - title_weight) * body_score


# ── 3~4) knn + 공고별 집계 ──────────────────────────────────────────

def knn_by_bid(
    vec: list[float], *, include_tables: bool, candidate_ids: list[str]
) -> dict[str, float]:
    """한 쿼리의 knn 결과 → {bid_id: 상위 N청크 평균 점수}.

    ⚠ 후보 공고(candidate_ids)로 검색을 미리 좁힌다. 예전엔 19,317건 전체에서
    knn을 돌린 뒤 후보로 걸렀는데, 공고 하나가 청크 30~114개라 상위 200청크면
    공고 2~6개뿐이었다 — 실제로 후보 449건 중 22건만 잡혔다. filter를 knn 안에
    넣으면 "후보 안에서의 상위"를 가져온다.
    """
    host = os.environ.get("OPENSEARCH_LOCAL_URL", "https://localhost:9243")
    auth = (os.environ["OPENSEARCH_USER"], os.environ["OPENSEARCH_PASSWORD"])

    filters: list[dict] = [{"terms": {"bid_id": candidate_ids}}]
    if not include_tables:
        filters.append({"term": {"type": "text"}})

    body: dict = {
        "size": CHUNKS_PER_QUERY,
        "query": {
            "knn": {
                "vector": {
                    "vector": vec,
                    "k": CHUNKS_PER_QUERY,
                    "filter": {"bool": {"filter": filters}},
                }
            }
        },
        "_source": ["bid_id", "type"],
    }

    r = requests.post(f"{host}/bid_chunks/_search", auth=auth, verify=False,
                      json=body, timeout=120)
    r.raise_for_status()
    per_bid: dict[str, list[float]] = defaultdict(list)
    for h in r.json()["hits"]["hits"]:
        per_bid[h["_source"]["bid_id"]].append(h["_score"])
    return {
        bid: sum(sorted(scores, reverse=True)[:TOP_CHUNKS_PER_BID])
        / min(len(scores), TOP_CHUNKS_PER_BID)
        for bid, scores in per_bid.items()
    }


# ── 6) 후보 제한 ────────────────────────────────────────────────────

def eligible_bids(cur, company_id: int) -> dict[str, str]:
    """{bid_id: verdict} — 불가 제외, 마감 전, 스크랩 제외."""
    cur.execute(
        """select b.bid_id, m.verdict
           from match_results m
           join bid_table b on b.bid_ntce_no=m.bid_ntce_no and b.bid_ntce_ord=m.bid_ntce_ord
           where m.company_id=%s
             and b.qual_status='merged'
             and b.bid_ntce_nm is not null
             and (b.bid_clse_dt >= now() or b.bid_clse_dt is null)
             and (m.verdict is null or m.verdict <> '불가')
             and not exists (
               select 1 from company_bid_scraps s
               where s.company_id=m.company_id
                 and s.bid_ntce_no=m.bid_ntce_no and s.bid_ntce_ord=m.bid_ntce_ord
             )""",
        (company_id,),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", type=int, required=True)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--include-tables", action="store_true",
                    help="table 청크도 검색에 포함(비교 실험용)")
    ap.add_argument(
        "--title-weight",
        type=float,
        default=DEFAULT_TITLE_WEIGHT,
        help="하이브리드 제목 가중치(0~1, 기본 0.8)",
    )
    ap.add_argument(
        "--shortlist",
        type=int,
        default=DEFAULT_SHORTLIST,
        help="제목 점수 상위 N건에 본문 점수를 계산(기본 100)",
    )
    args = ap.parse_args()
    if not 0.0 <= args.title_weight <= 1.0:
        ap.error("--title-weight는 0과 1 사이여야 합니다.")
    if args.shortlist < args.top:
        ap.error("--shortlist는 --top 이상이어야 합니다.")

    load_dotenv(".env")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("select name from companies where id=%s", (args.company,))
            row = cur.fetchone()
            name = row[0] if row else f"(id={args.company})"

            queries, source = build_queries(cur, args.company)
            candidates = eligible_bids(cur, args.company)

            cur.execute(
                """select bid_id, bid_ntce_nm from bid_table
                   where bid_id = any(%s)""",
                (list(candidates),),
            )
            titles = {r[0]: r[1] for r in cur.fetchall()}

        print(f"# {name} 추천 검증")
        print()
        print(f"- 쿼리 출처: **{source}** ({len(queries)}개)")
        for q in queries:
            print(f"  - {q}")
        print(f"- 후보 공고(불가 제외·마감 전·스크랩 제외): **{len(candidates):,}건**")
        print(f"- 청크 필터: {'text+table 전체' if args.include_tables else 'text만'}")
        print()

        if not queries:
            print("쿼리를 만들 재료가 없습니다(실적·품목·면허 전무).")
            return
        if not candidates:
            print("자격 판정을 통과한 후보 공고가 없습니다.")
            return

        cand_ids = list(candidates)
        query_vecs = embed(queries)
        title_vecs = embed([titles[bid] for bid in cand_ids])
        title_best, title_hit_by = title_scores(
            queries,
            query_vecs,
            {bid: titles[bid] for bid in cand_ids},
            title_vecs,
        )
        shortlist_ids = [
            bid
            for bid, _ in sorted(title_best.items(), key=lambda item: -item[1])[
                : args.shortlist
            ]
        ]

        body_best: dict[str, float] = {}
        body_hit_by: dict[str, str] = {}
        for q, vec in zip(queries, query_vecs):
            for bid, score in knn_by_bid(
                vec, include_tables=args.include_tables, candidate_ids=shortlist_ids
            ).items():
                if bid not in title_best:
                    continue
                if bid not in body_best or score > body_best[bid]:
                    body_best[bid] = score
                    body_hit_by[bid] = q

        # 본문 kNN에 잡히지 않은 공고는 하이브리드 비교에서 제외한다. 0점으로
        # 채우면 실제 관련도 부족과 검색 누락을 혼동해 제목 점수를 부당하게 깎는다.
        ranked = sorted(
            (
                (
                    bid,
                    hybrid_score(title_best[bid], body_best[bid], args.title_weight),
                )
                for bid in shortlist_ids
                if bid in body_best
            ),
            key=lambda item: -item[1],
        )[: args.top]
        if not ranked:
            print("제목 shortlist 안에서 본문 청크가 잡힌 공고가 없습니다.")
            return

        print(
            f"- 하이브리드: 제목 {args.title_weight:.0%} + "
            f"본문 {1.0 - args.title_weight:.0%}"
        )
        print(f"- 본문 재정렬 대상: 제목 상위 {len(shortlist_ids)}건")
        print()
        print(f"## 상위 {len(ranked)}개 (컷 미적용 - 점수 분포 확인용)")
        print()
        print("| 순위 | 혼합 | 제목 | 본문 | 판정 | 공고명 | 제목 적중 | 본문 적중 |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for i, (bid, score) in enumerate(ranked, 1):
            title = (titles.get(bid) or "")[:45]
            print(
                f"| {i} | {score:.4f} | {title_best[bid]:.4f} "
                f"| {body_best[bid]:.4f} | {candidates[bid] or '-'} | {title} "
                f"| {title_hit_by[bid][:30]} | {body_hit_by[bid][:30]} |"
            )

        scores = [s for _, s in ranked]
        print()
        print(f"- 점수 범위: {max(scores):.4f} ~ {min(scores):.4f} "
              f"(1위 대비 최하위 {min(scores) / max(scores) * 100:.1f}%)")
        print(
            f"- 본문 점수가 계산된 shortlist: "
            f"{len(body_best):,}건 / {len(shortlist_ids):,}건"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
