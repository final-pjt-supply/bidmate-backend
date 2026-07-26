# -*- coding: utf-8 -*-
"""제목 유사도 단독 검증 — 버리는 코드 (이슈 #56).

왜 필요한가:
  본문 청크 검색만으로는 점수 폭이 2~3%로 수렴해 컷을 그을 수 없었다(0.765~0.788).
  공고 문서 대부분이 정형 문구라 어떤 쿼리든 모든 공고가 비슷한 점수를 받는다.
  B회사(네트워크)는 어느 정도 갈렸지만 A회사(CCTV)는 CCTV 공고 29건 중 하나도
  상위에 못 올렸다.

  하이브리드(본문 + 제목)로 가기 전에, 제목 유사도 단독으로 A가 고쳐지는지부터
  확인한다. 여기서도 안 되면 하이브리드도 의미가 없다.

방법:
  후보 공고 제목 전부 + 회사 쿼리를 Cloudflare BGE-M3로 임베딩해 코사인 유사도.
  OpenSearch를 쓰지 않는다 — 449건 제목은 메모리에서 바로 계산하는 게 빠르다.
  (프로덕션에선 제목 벡터를 인덱스에 넣어야 하지만, 검증엔 불필요.)

사용법:
    python scripts/try_title_sim.py --company 11
"""
from __future__ import annotations

import argparse
import math
import os

import psycopg
import requests
from dotenv import load_dotenv

EMBED_BATCH = 100   # Cloudflare 한 번에 보낼 텍스트 수


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


def embed(texts: list[str]) -> list[list[float]]:
    acct = os.environ["CF_ACCOUNT_ID"]
    token = os.environ["CF_API_TOKEN"]
    model = os.environ.get("CF_EMBEDDING_MODEL", "@cf/baai/bge-m3")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                          json={"text": batch}, timeout=120)
        r.raise_for_status()
        d = r.json()
        if not d.get("success"):
            raise RuntimeError(f"임베딩 실패: {str(d)[:300]}")
        out.extend(d["result"]["data"])
        print(f"  임베딩 {min(i + EMBED_BATCH, len(texts))}/{len(texts)}", flush=True)
    return out


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", type=int, required=True)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    load_dotenv(".env")
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute("select name from companies where id=%s", (args.company,))
            row = cur.fetchone()
            name = row[0] if row else f"(id={args.company})"

            cur.execute(
                """select contract_name from company_performance_records
                   where company_id=%s and contract_name is not null
                   order by end_date desc nulls last limit 10""",
                (args.company,),
            )
            queries = [r[0] for r in cur.fetchall()]
            if not queries:
                cur.execute(
                    "select item_name from company_items where company_id=%s and item_name is not null",
                    (args.company,),
                )
                queries = [r[0] for r in cur.fetchall()]

            cur.execute(
                """select b.bid_id, b.bid_ntce_nm, m.verdict
                   from match_results m
                   join bid_table b on b.bid_ntce_no=m.bid_ntce_no and b.bid_ntce_ord=m.bid_ntce_ord
                   where m.company_id=%s
                     and b.qual_status='merged'
                     and (b.bid_clse_dt >= now() or b.bid_clse_dt is null)
                     and (m.verdict is null or m.verdict <> '불가')
                     and b.bid_ntce_nm is not null""",
                (args.company,),
            )
            cands = cur.fetchall()

        print(f"# {name} — 제목 유사도 단독")
        print()
        print(f"- 쿼리 {len(queries)}개")
        for q in queries:
            print(f"  - {q}")
        print(f"- 후보 {len(cands)}건")
        print()

        print("임베딩 중...")
        qvecs = embed(queries)
        tvecs = embed([c[1] for c in cands])
        print()

        scored = []
        for (bid_id, title, verdict), tv in zip(cands, tvecs):
            best, best_q = 0.0, ""
            for q, qv in zip(queries, qvecs):
                s = cosine(qv, tv)
                if s > best:
                    best, best_q = s, q
            scored.append((best, title, verdict, best_q))
        scored.sort(reverse=True)

        print(f"## 상위 {args.top}개")
        print()
        print("| 순위 | 점수 | 판정 | 공고명 | 적중 쿼리 |")
        print("| --- | --- | --- | --- | --- |")
        for i, (s, title, verdict, q) in enumerate(scored[: args.top], 1):
            print(f"| {i} | {s:.4f} | {verdict or '-'} | {title[:45]} | {q[:30]} |")

        top = [s for s, *_ in scored[: args.top]]
        allsc = [s for s, *_ in scored]
        print()
        print(f"- 상위{args.top} 점수 범위: {max(top):.4f} ~ {min(top):.4f} "
              f"(1위 대비 {min(top) / max(top) * 100:.1f}%)")
        print(f"- 전체 범위: {max(allsc):.4f} ~ {min(allsc):.4f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
