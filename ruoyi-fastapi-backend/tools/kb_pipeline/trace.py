"""
【已失效 2026-11-10】本工具读取 v0.8 已删除的 PICO 表（kb_finding、kb_population、kb_arm…），
事实已改存 kb_edge；Neo4j 副本未部署，需要时按 kb_edge 重写后再用。

图谱 → 原文回溯：给图中任一节点 uid，或任一条边上的 (source_table, source_id)，
返回它出自哪篇文献、第几页、哪个原文块，以及原文片段（kb_span_link → kb_span → kb_block）

    python -m tools.kb_pipeline.trace <节点uid>
    python -m tools.kb_pipeline.trace --row kb_rec_action 12

边的回溯规则：
- HAS_CONDITION / RECEIVES / CONDITION / ACTION 边带 source_table + source_id，按该行回溯；
- 其余边（EXPOSURE、OUTCOME、HAS_RESULT、OF_STUDY 等）归源节点所有，回溯源节点即可。
"""

import argparse

import psycopg2

from tools.kb_pipeline.graph import Cursor
from tools.kb_pipeline.ingest import db_dsn

SPANS_SQL = """
SELECT l.role, lr.source_id, lr.title, s.page, b.block_key, b.section, s.quote_verified, s.quote, b.bbox
FROM kb_span_link l JOIN kb_span s USING (span_id) JOIN kb_block b USING (block_id)
JOIN kb_document_version v ON v.version_id = s.version_id JOIN kb_document d ON d.document_id = v.document_id
LEFT JOIN lit_record lr ON lr.record_id = d.record_id
WHERE l.entity_type = %s AND l.entity_id = ANY(%s::bigint[])
ORDER BY s.page, b.seq
"""
SPAN_FIELDS = ('role', 'source_id', 'title', 'page', 'block_key', 'section', 'quote_verified', 'quote', 'bbox')

# 节点：(图标签, 表, 主键, span_link 实体类型)；实体类型为 None 的需走兜底
NODE_TABLES = [
    ('Finding', 'kb_finding', 'finding_id', 'finding'),
    ('Result', 'kb_finding_result', 'result_id', None),
    ('Population', 'kb_population', 'population_id', 'population'),
    ('Arm', 'kb_arm', 'arm_id', None),
    ('Outcome', 'kb_outcome', 'outcome_id', 'outcome'),
    ('Study', 'kb_study', 'study_id', 'study'),
    ('EffectModification', 'kb_effect_modification', 'em_id', 'effect_modification'),
    ('Assertion', 'kb_assertion', 'assertion_id', 'assertion'),
    ('Recommendation', 'kb_recommendation', 'rec_id', 'recommendation'),
    ('Concept', 'kb_concept', 'concept_id', None),
    ('Document', 'kb_document', 'document_id', None),
]
# 边：source_table → (span_link 实体类型, 无原文时回退到的父实体 SQL)
ROW_TABLES = {
    'kb_population_condition': (
        'population_condition',
        ('population', 'SELECT population_id FROM kb_population_condition WHERE condition_id = %s'),
    ),
    'kb_arm_intervention': ('arm_intervention', None),
    'kb_rec_condition': ('recommendation', None),
    'kb_rec_action': ('recommendation', None),
}
ROW_PARENT_SQL = {
    'kb_rec_condition': 'SELECT rec_id FROM kb_rec_condition WHERE rec_condition_id = %s',
    'kb_rec_action': 'SELECT rec_id FROM kb_rec_action WHERE rec_action_id = %s',
}


def _ids(cur: Cursor, sql: str, args: tuple) -> list[int]:
    cur.execute(sql, args)
    return [r[0] for r in cur.fetchall()]


def spans(cur: Cursor, entity_type: str, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    cur.execute(SPANS_SQL, (entity_type, ids))
    return [dict(zip(SPAN_FIELDS, r, strict=True)) for r in cur.fetchall()]


def _fallback(cur: Cursor, label: str, pk: int) -> tuple[str, list[dict]]:
    """
    本身不挂原文的节点：结果 → 所属发现；组别 → 其干预；概念 → 引用它的发现；文献 → 仅文献信息
    """
    if label == 'Result':
        ids = _ids(cur, 'SELECT finding_id FROM kb_finding_result WHERE result_id = %s', (pk,))
        return '经所属 Finding', spans(cur, 'finding', ids)
    if label == 'Arm':
        ids = _ids(cur, 'SELECT arm_intervention_id FROM kb_arm_intervention WHERE arm_id = %s', (pk,))
        return '经组别干预', spans(cur, 'arm_intervention', ids)
    if label == 'Concept':
        ids = _ids(
            cur,
            """SELECT finding_id FROM kb_finding f LEFT JOIN kb_outcome o USING (outcome_id)
               WHERE %s IN (f.exposure_concept_id, f.comparator_concept_id, o.concept_id) ORDER BY finding_id""",
            (pk,),
        )
        return f'经引用该概念的 {len(ids)} 个 Finding', spans(cur, 'finding', ids)
    cur.execute(
        """SELECT lr.source_id, lr.title, v.file_path FROM kb_document d LEFT JOIN lit_record lr USING (record_id)
           LEFT JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current WHERE d.document_id = %s""",
        (pk,),
    )
    r = cur.fetchone()
    return '文献节点本身', [{'source_id': r[0], 'title': r[1], 'file_path': r[2]}] if r else []


def trace_node(cur: Cursor, uid: str) -> dict:
    for label, table, pk_col, link_type in NODE_TABLES:
        cur.execute(f'SELECT {pk_col} FROM {table} WHERE uid::text = %s', (uid,))
        row = cur.fetchone()
        if not row:
            continue
        if link_type:
            found = spans(cur, link_type, [row[0]])
            if found or label != 'Outcome':
                return {'label': label, 'via': '直接', 'spans': found}
            ids = _ids(cur, 'SELECT finding_id FROM kb_finding WHERE outcome_id = %s', (row[0],))
            return {'label': label, 'via': '经使用该结局的 Finding', 'spans': spans(cur, 'finding', ids)}
        via, found = _fallback(cur, label, row[0])
        return {'label': label, 'via': via, 'spans': found}
    return {'label': None, 'via': '未找到该 uid', 'spans': []}


def trace_row(cur: Cursor, table: str, row_id: int) -> dict:
    if table not in ROW_TABLES:
        return {'label': table, 'via': f'不支持的 source_table（可选：{", ".join(ROW_TABLES)}）', 'spans': []}
    link_type, parent = ROW_TABLES[table]
    if table in ROW_PARENT_SQL:
        return {
            'label': table,
            'via': '经所属推荐意见',
            'spans': spans(cur, link_type, _ids(cur, ROW_PARENT_SQL[table], (row_id,))),
        }
    found = spans(cur, link_type, [row_id])
    if found or parent is None:
        return {'label': table, 'via': '直接', 'spans': found}
    parent_type, sql = parent
    return {'label': table, 'via': '经所属人群', 'spans': spans(cur, parent_type, _ids(cur, sql, (row_id,)))}


def show(result: dict, width: int = 200) -> None:
    print(f'[{result["label"]}] 回溯方式：{result["via"]}，原文片段 {len(result["spans"])} 条')
    for s in result['spans']:
        if 'quote' not in s:
            print(f'  文献 {s["source_id"]}《{(s["title"] or "")[:60]}》 {s["file_path"]}')
            continue
        flag = '已核验' if s['quote_verified'] else '未核验'
        print(f'  文献 {s["source_id"]} 第{s["page"]}页 块{s["block_key"]} [{s["section"]}] {s["role"]} {flag}')
        print(f'    《{(s["title"] or "")[:60]}》')
        print(f'    “{s["quote"][:width]}”')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('uid', nargs='?', help='图节点 uid')
    ap.add_argument('--row', nargs=2, metavar=('SOURCE_TABLE', 'SOURCE_ID'), help='边上的 source_table source_id')
    ap.add_argument('--env', default='dev')
    a = ap.parse_args()
    if not a.uid and not a.row:
        ap.error('需要 uid 或 --row')
    with psycopg2.connect(db_dsn(a.env)) as conn, conn.cursor() as cur:
        show(trace_row(cur, a.row[0], int(a.row[1])) if a.row else trace_node(cur, a.uid))


if __name__ == '__main__':
    main()
