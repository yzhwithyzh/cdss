"""
【已失效 2026-11-10】本工具读取 v0.8 已删除的 PICO 表（kb_finding、kb_population、kb_arm…），
事实已改存 kb_edge；Neo4j 副本未部署，需要时按 kb_edge 重写后再用。

图谱验收查询：在真实数据上检查图查询可用、与 PG 一致、可回溯原文

    python -m tools.kb_pipeline.verify_graph --env dev
"""

import argparse

import psycopg2

from tools.kb_pipeline.graph import Neo4j
from tools.kb_pipeline.ingest import db_dsn

QUERIES = [
    (
        '1 中文检索“冷EMR”→ 作为暴露（含组别推导）的研究结果来自哪些文献',
        """CALL db.index.fulltext.queryNodes('concept_names', '冷EMR') YIELD node AS c, score
           MATCH (c)<-[:EXPOSURE]-(f:Finding)-[:FROM_DOCUMENT]->(d:Document)
           RETURN c.name_zh AS 概念, d.source_id AS 文献, count(DISTINCT f) AS 结果数 ORDER BY 结果数 DESC LIMIT 5""",
    ),
    (
        '2 41115：幽门螺杆菌感染与腺瘤复发（单因素/多因素 OR，结局范围）',
        """MATCH (d:Document {source_id: '41115'})<-[:FROM_DOCUMENT]-(f:Finding)-[:EXPOSURE]->(c:Concept)
           WHERE toLower(c.name_en) CONTAINS 'pylori' OR c.name_zh CONTAINS '幽门'
           MATCH (f)-[:HAS_RESULT]->(r:Result {measure: 'or'})
           OPTIONAL MATCH (f)-[:OUTCOME]->(o:Outcome)
           RETURN f.model_type AS 模型, r.value_as_reported AS OR值, o.outcome_scope AS 结局范围""",
    ),
    (
        '3 患者匹配：最大径 6 mm 的病灶落入哪些研究人群的纳入/亚组条件',
        """MATCH (p:Population)-[c:HAS_CONDITION]->(:Concept)
           WHERE c.unit = 'mm' AND coalesce(c.value_low, -1e9) <= 6 AND 6 <= coalesce(c.value_high, 1e9)
           MATCH (p)-[:OF_STUDY]->(:Study)-[:REPORTED_IN]->(d:Document)
           RETURN d.source_id AS 文献, p.role AS 角色, p.label AS 人群, c.kind AS 条件, c.value_as_reported AS 原文
           ORDER BY 文献 LIMIT 8""",
    ),
    (
        '4 效应修饰：哪些研究报告了交互检验',
        """MATCH (em:EffectModification)-[:MODIFIER]->(m:Concept), (em)-[:OF_STUDY]->(:Study)-[:REPORTED_IN]->(d:Document)
           RETURN d.source_id AS 文献, coalesce(m.name_zh, m.name_en) AS 修饰因素, em.has_interaction_test AS 有交互检验,
                  em.interaction_p_as_reported AS P值""",
    ),
    (
        '5 指南推荐：随访类推荐的动作与间隔',
        """MATCH (g:Guideline)-[:HAS_RECOMMENDATION]->(r:Recommendation)-[a:ACTION]->()
           WHERE a.action_type = 'surveillance_exam'
           RETURN r.number AS 编号, a.interval_low AS 间隔下限, a.interval_high AS 间隔上限, a.interval_unit AS 单位,
                  left(a.text, 60) AS 原文 LIMIT 6""",
    ),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', default='dev')
    a = ap.parse_args()
    neo = Neo4j(a.env)
    for title, q in QUERIES:
        data = neo.run(q)
        print(f'\n■ {title}\n  {data["fields"]}')
        for row in data['values']:
            print('  ', row)

    print('\n■ 6 PG 与图谱数量一致性')
    with psycopg2.connect(db_dsn(a.env)) as conn, conn.cursor() as cur:
        for label, table in [
            ('Finding', 'kb_finding'),
            ('Result', 'kb_finding_result'),
            ('Population', 'kb_population'),
            ('Study', 'kb_study'),
            ('Recommendation', 'kb_recommendation'),
            ('Concept', 'kb_concept'),
        ]:
            cur.execute(f'SELECT count(*) FROM {table}')
            pg = cur.fetchone()[0]
            g = neo.run(f'MATCH (n:{label}) RETURN count(n)')['values'][0][0]
            print(f'  {label:<15} PG {pg:>5}  图 {g:>5}  {"一致" if pg == g else "不一致"}')

        print('\n■ 7 从图谱节点回溯原文：41115 多因素幽门螺杆菌 OR 的原文片段')
        uid = neo.run(
            """MATCH (d:Document {source_id: '41115'})<-[:FROM_DOCUMENT]-(f:Finding {model_type: 'adjusted'})-[:EXPOSURE]->(c)
               WHERE toLower(c.name_en) CONTAINS 'pylori' OR c.name_zh CONTAINS '幽门' RETURN f.uid LIMIT 1"""
        )['values']
        if uid:
            cur.execute(
                """SELECT s.page, b.block_key, b.section, s.quote_verified, left(s.quote, 160)
                   FROM kb_finding f JOIN kb_span_link l ON l.entity_type = 'finding' AND l.entity_id = f.finding_id
                   JOIN kb_span s USING (span_id) JOIN kb_block b USING (block_id) WHERE f.uid = %s""",
                (uid[0][0],),
            )
            for row in cur.fetchall():
                print('  ', row)


if __name__ == '__main__':
    main()
