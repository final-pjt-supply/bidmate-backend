# -*- coding: utf-8 -*-
"""공고 통계 집계 matview — institution_parent, bid_stats

원본 bid_table에 요청마다 집계를 날리지 않기 위해 결과를 물질화한다.
테이블이 아니라 matview인 이유는 집계가 하나의 SELECT로 완전히 표현되기 때문이다.
내용이 정의로부터만 나오므로 앱이 실수로 오염시킬 수 없고, 로직이 DB 밖으로
표류하지 않는다.

여기 담긴 집계 규칙은 전부 실측으로 확인된 데이터 함정 처리다(원본:
bidmate-frontend/scripts/gen-stats-mock.py). SQL 이식본을 파이썬 원본과 대조해
기관명 병합 413건 완전 일치, 45개 조건 × 8항목 일치를 확인했다.

빌드 시간 institution_parent 842ms + bid_stats 5.7초. WITH DATA로 만든다 —
NO DATA면 첫 REFRESH 전까지 /stats가 빈 응답이고, CONCURRENTLY도 못 쓴다.

Revision ID: 0006_bid_stats_matviews
Revises: 0005_chat_daily_usage
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006_bid_stats_matviews"
down_revision: Union[str, None] = "0005_chat_daily_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 하위조직임을 알려주는 꼬리말. 이게 붙은 경우에만 상위기관으로 묶는다.
SUBUNIT = (
    "(본부|지사|지역본부|사업소|사업단|관리단|출장소|센터|지부|지원단"
    "|사무소|영업소|지방청|세관|우체국|보건소|지청)$"
)
# 이름이 겹쳐도 별개 발주 주체다. "충청북도"와 "충청북도교육청"은 예산도 담당자도 다르다.
INDEPENDENT = (
    "(교육청|대학교|대학|공사|공단|재단|진흥원|연구원|병원|공항공사"
    "|시설관리공단|도시공사|의료원|과학관|박물관)$"
)

MAS_PLACEHOLDER = "각 수요기관"

INSTITUTION_PARENT = f"""
CREATE MATERIALIZED VIEW institution_parent AS
WITH names AS (
  SELECT DISTINCT dminstt_nm AS nm, regexp_replace(dminstt_nm, '\\s+', '', 'g') AS k
  FROM bid_table WHERE dminstt_nm IS NOT NULL
),
cand AS (
  SELECT DISTINCT ON (c.k) c.nm, c.k, p.nm AS parent_nm, p.k AS pk
  FROM names c
  JOIN names p
    ON length(p.k) >= 4
   AND length(p.k) < length(c.k)
   AND c.k LIKE p.k || '%'
  ORDER BY c.k, length(p.k)
)
SELECT nm AS name, parent_nm AS parent
FROM cand
WHERE substr(k, length(pk)+1) ~ '{SUBUNIT}'
  AND substr(k, length(pk)+1) !~ '{INDEPENDENT}'
"""

BID_STATS = f"""
CREATE MATERIALIZED VIEW bid_stats AS
WITH
scope AS (
  SELECT b.bid_id, b.bid_category, b.dminstt_nm, b.presmpt_prce, b.bdgt_amt,
         to_char(b.bid_ntce_dt, 'YYYY-MM') AS m
  FROM bid_table b
  WHERE b.bid_ntce_dt < date_trunc('month', CURRENT_DATE)
),
period AS (SELECT min(m) AS m_from, max(m) AS m_to FROM scope),
months AS (
  SELECT to_char(gs, 'YYYY-MM') AS m
  FROM period p,
       generate_series(to_date(p.m_from,'YYYY-MM'), to_date(p.m_to,'YYYY-MM'),
                       interval '1 month') gs
),
axis AS (
  SELECT s.*, c.category, t.tag
  FROM scope s
  CROSS JOIN LATERAL (VALUES (s.bid_category), ('ALL')) AS c(category)
  CROSS JOIN LATERAL (
    SELECT 'ALL'::text AS tag
    UNION ALL
    SELECT g.tag FROM bid_tags g
     WHERE g.bid_id = s.bid_id AND right(g.source, 4) <> '_low'
  ) AS t
),
tag_rank AS (
  SELECT category, tag, count(*) AS cnt,
         row_number() OVER (PARTITION BY category ORDER BY count(*) DESC, tag) AS rn
  FROM axis WHERE tag <> 'ALL' GROUP BY 1, 2
),
top_tags AS (SELECT category, tag, cnt FROM tag_rank WHERE rn <= 8),
keys AS (
  SELECT DISTINCT category, 'ALL'::text AS tag FROM axis
  UNION
  SELECT category, tag FROM top_tags
),
no_amount AS (
  SELECT bid_category FROM scope
  GROUP BY 1 HAVING coalesce(sum(presmpt_prce), 0) = 0 AND count(bdgt_amt) = 0
),
totals AS (SELECT category, tag, count(*) AS cnt FROM axis GROUP BY 1, 2),
monthly AS (
  SELECT k.category, k.tag, mo.m, count(a.bid_id) AS cnt
  FROM keys k
  CROSS JOIN months mo
  LEFT JOIN axis a ON a.category = k.category AND a.tag = k.tag AND a.m = mo.m
  GROUP BY 1, 2, 3
),
budget AS (
  SELECT category, tag,
         CASE WHEN presmpt_prce < 5e7 THEN 0 WHEN presmpt_prce < 2e8 THEN 1
              WHEN presmpt_prce < 1e9 THEN 2 WHEN presmpt_prce < 5e9 THEN 3
              ELSE 4 END AS b,
         count(*) AS cnt
  FROM axis
  WHERE NOT (category = 'ALL' AND bid_category IN (SELECT bid_category FROM no_amount))
  GROUP BY 1, 2, 3
),
inst AS (
  SELECT a.category, a.tag, coalesce(p.parent, a.dminstt_nm) AS name, count(*) AS cnt
  FROM axis a
  LEFT JOIN institution_parent p ON p.name = a.dminstt_nm
  WHERE a.dminstt_nm <> '{MAS_PLACEHOLDER}'
  GROUP BY 1, 2, 3
),
inst_rank AS (
  SELECT *, row_number() OVER (PARTITION BY category, tag ORDER BY cnt DESC, name) AS rn
  FROM inst
),
excluded_mas AS (
  SELECT category, tag, count(*) AS cnt FROM axis
  WHERE dminstt_nm = '{MAS_PLACEHOLDER}' GROUP BY 1, 2
)
SELECT
  k.category,
  k.tag,
  jsonb_build_object(
    'period', (SELECT jsonb_build_object('from', m_from, 'to', m_to) FROM period),
    'total', coalesce(t.cnt, 0),
    'amount_available', k.category <> ALL (SELECT bid_category FROM no_amount),
    'tags', coalesce(
      (SELECT jsonb_agg(jsonb_build_object('tag', tt.tag, 'cnt', tt.cnt)
                        ORDER BY tt.cnt DESC, tt.tag)
         FROM top_tags tt WHERE tt.category = k.category), '[]'::jsonb),
    'monthly', coalesce(
      (SELECT jsonb_agg(jsonb_build_object('m', mn.m, 'cnt', mn.cnt) ORDER BY mn.m)
         FROM monthly mn WHERE mn.category = k.category AND mn.tag = k.tag), '[]'::jsonb),
    'budget', jsonb_build_object(
      'buckets', coalesce(
        (SELECT jsonb_agg(jsonb_build_object('b', bg.b, 'cnt', bg.cnt) ORDER BY bg.b)
           FROM budget bg WHERE bg.category = k.category AND bg.tag = k.tag), '[]'::jsonb),
      'total', coalesce(
        (SELECT sum(bg.cnt) FROM budget bg
          WHERE bg.category = k.category AND bg.tag = k.tag), 0)),
    'institutions', coalesce(
      (SELECT jsonb_agg(jsonb_build_object('name', ir.name, 'cnt', ir.cnt) ORDER BY ir.rn)
         FROM inst_rank ir
        WHERE ir.category = k.category AND ir.tag = k.tag AND ir.rn <= 8), '[]'::jsonb),
    'excluded_mas', coalesce(
      (SELECT em.cnt FROM excluded_mas em
        WHERE em.category = k.category AND em.tag = k.tag), 0)
  ) AS payload,
  now() AS computed_at
FROM keys k
LEFT JOIN totals t ON t.category = k.category AND t.tag = k.tag
"""


def upgrade() -> None:
    # 순서 중요 — bid_stats가 institution_parent를 참조한다.
    op.execute(INSTITUTION_PARENT)
    # CONCURRENTLY 갱신에 필요하다(unique index가 없으면 REFRESH ... CONCURRENTLY 불가).
    op.execute("CREATE UNIQUE INDEX institution_parent_name_idx ON institution_parent (name)")
    op.execute(BID_STATS)
    op.execute("CREATE UNIQUE INDEX bid_stats_key_idx ON bid_stats (category, tag)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS bid_stats")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS institution_parent")
