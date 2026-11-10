"""
补齐概念关系的原文链接：审核时拆分出来的关系（source=review）只在备注里写了原句，原文片段链接还挂在被拒绝的原关系上。
按备注中的“文献 <source_id> 原句“…””找到该文献里的同一片段，补 kb_span_link；
已有片段里没有（如重新抽取后该句没被抽到）时，在该文献当前版本的原文块里逐字定位这句话，新建片段再链接。
仍找不到的列出来，不猜。load.py 每次入库后自动执行，避免重抽/重载后审核过的关系丢失原文出处。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.relation_spans [--dry-run]
"""

import argparse
import json
import re

import psycopg2

from tools.kb_pipeline.ingest import db_dsn

ACTOR = 'system:relation_spans'


def norm_ws(s: str) -> str:
    return re.sub(r'\s+', ' ', s or '').strip()


def new_span(cur, source_id: str, quote: str) -> tuple[int] | None:
    """
    在该文献当前版本的原文块里逐字定位备注原句（忽略空白差异），新建 kb_span
    """
    from tools.kb_pipeline.load import locate  # noqa: PLC0415  避免循环导入

    cur.execute("""
        SELECT b.block_id, b.page, b.text, v.version_id FROM kb_block b JOIN kb_document_version v ON v.version_id = b.version_id AND v.is_current
        JOIN kb_document d ON d.document_id = v.document_id JOIN lit_record r ON r.record_id = d.record_id
        WHERE r.source_id = %s ORDER BY b.seq""", (source_id,))
    for block_id, page, text, version_id in cur.fetchall():
        where = locate(quote, text or '')
        if where:
            cur.execute("""INSERT INTO kb_span(version_id, block_id, page, quote, char_start, char_end, quote_verified)
                           VALUES (%s, %s, %s, %s, %s, %s, true) RETURNING span_id""",
                        (version_id, block_id, page, text[where[0]:where[1]], where[0], where[1]))
            return cur.fetchone()
    return None


def repair(cur) -> dict:
    cur.execute("""
        SELECT r.relation_id, r.note FROM kb_concept_relation r
        WHERE r.review_status IS DISTINCT FROM 'rejected' AND r.source IN ('literature', 'review')
          AND NOT EXISTS (SELECT 1 FROM kb_span_link sl WHERE sl.entity_type = 'concept_relation' AND sl.entity_id = r.relation_id)
        ORDER BY r.relation_id""")
    linked, missing = [], []
    for rid, note in cur.fetchall():
        doc = re.search(r'文献\s*(\d+)', note or '')
        quote = re.search(r'原句[“"](.+?)(?:[”"]|$)', note or '', re.S)
        if not doc or not quote:
            missing.append((rid, '备注里没有文献编号或原句'))
            continue
        needle = norm_ws(quote.group(1))[:60]
        cur.execute("""
            SELECT s.span_id FROM kb_span s JOIN kb_document_version v ON v.version_id = s.version_id AND v.is_current
            JOIN kb_document d ON d.document_id = v.document_id JOIN lit_record r ON r.record_id = d.record_id
            WHERE r.source_id = %s AND regexp_replace(s.quote, '\\s+', ' ', 'g') LIKE %s
            ORDER BY (EXISTS (SELECT 1 FROM kb_span_link x WHERE x.span_id = s.span_id AND x.entity_type = 'concept_relation')) DESC,
                     s.span_id LIMIT 1""", (doc.group(1), needle.replace('%', r'\%').replace('_', r'\_') + '%'))
        row = cur.fetchone() or new_span(cur, doc.group(1), norm_ws(quote.group(1)))
        if not row:
            missing.append((rid, f'文献 {doc.group(1)} 中找不到以“{needle[:30]}…”开头的片段'))
            continue
        cur.execute("""INSERT INTO kb_span_link(entity_type, entity_id, span_id, role)
                       SELECT 'concept_relation', %s, %s, 'source'
                       WHERE NOT EXISTS (SELECT 1 FROM kb_span_link WHERE entity_type = 'concept_relation' AND entity_id = %s AND span_id = %s)""",
                    (rid, row[0], rid, row[0]))
        cur.execute("""INSERT INTO kb_audit_event(entity_type, entity_id, action, changes, actor, reason)
                       VALUES ('concept_relation', %s, 'update', %s, %s, %s)""",
                    (rid, json.dumps({'span_link_added': row[0]}), ACTOR, '拆分审核时原文链接未随关系迁移，按备注原句补齐'))
        linked.append((rid, row[0]))
    return {'linked': linked, 'missing': missing}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', default='dev')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    with psycopg2.connect(db_dsn(a.env)) as conn, conn.cursor() as cur:
        print(json.dumps(repair(cur), ensure_ascii=False, indent=1))
        if a.dry_run:
            conn.rollback()
            print('dry-run：已回滚')


if __name__ == '__main__':
    main()
