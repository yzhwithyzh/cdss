"""
已入库文献的解析块回填（不重新入库，已有原文片段引用不受影响）：
- 按新规则重算章节（致谢/基金等归 backmatter，其后的正文标题不再沿用）与标题层级；
- 舍弃 backmatter 块，以及非指南/共识/系统综述的参考文献块（被原文片段引用的块保留并报告）；
- 写入 search_text（jieba 分词）与文献正文语言 body_language。
超长表格不在此拆分（拆分会改变已被引用的块），重新入库时由 ingest 拆分。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.fix_blocks --env dev [--ids-file ids.txt] [--dry-run]
不给 ID 时处理全部当前版本文献。
"""

import argparse
import os
from collections import Counter
from pathlib import Path

import psycopg2
from dotenv import dotenv_values

from tools.kb_pipeline.blocks import TABLE_SPLIT_CHARS, body_language, keep_block, search_text
from tools.kb_pipeline.ingest import db_dsn, keeps_references, locate_sources
from tools.pdf_parsing.config import BACKEND_ROOT, PdfParseSettings


def fix_one(cur: psycopg2.extensions.cursor, source_id: str, data_root: Path, settings: PdfParseSettings) -> dict:
    cur.execute(
        """SELECT r.record_id, d.document_id, v.version_id FROM lit_record r
           JOIN kb_document d ON d.record_id = r.record_id
           JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current
           WHERE r.source_id = %s""",
        (source_id,),
    )
    row = cur.fetchone()
    if not row:
        return {'error': '未入库'}
    record_id, document_id, version_id = row
    src = locate_sources(source_id, data_root, settings)
    if not src:
        return {'error': '找不到全文或解析结果'}
    rebuilt = {b.block_key: b for b in src['blocks']}
    keep_refs = keeps_references(cur, record_id)
    cur.execute(
        """SELECT b.block_id, b.block_key, b.text, b.block_type,
                  EXISTS (SELECT 1 FROM kb_span s WHERE s.block_id = b.block_id)
           FROM kb_block b WHERE b.version_id = %s""",
        (version_id,),
    )
    stats: Counter = Counter()
    drop: list[int] = []
    for block_id, key, text, block_type, cited in cur.fetchall():
        nb = rebuilt.get(key)
        if nb is None or nb.text != text:
            stats['key_mismatch'] += 1
            continue
        if not keep_block(nb, keep_refs):
            if cited:
                stats[f'kept_cited_{nb.section}'] += 1
            else:
                drop.append(block_id)
                stats[f'drop_{nb.section}'] += 1
                continue
        cur.execute(
            'UPDATE kb_block SET section = %s, heading_level = %s, search_text = %s WHERE block_id = %s',
            (nb.section, nb.heading_level, search_text(text), block_id),
        )
        stats['updated'] += 1
        if block_type == 'table' and len(text) > TABLE_SPLIT_CHARS:
            stats['big_table_unsplit'] += 1
    if drop:
        cur.execute('DELETE FROM kb_block WHERE block_id = ANY(%s::bigint[])', (drop,))
    language = body_language([b for b in src['blocks'] if keep_block(b, keep_refs)])
    cur.execute('UPDATE kb_document SET body_language = %s WHERE document_id = %s', (language, document_id))
    return dict(stats) | {'keep_references': keep_refs, 'language': language}


def main() -> None:
    ap = argparse.ArgumentParser(description='已入库文献的解析块回填')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--ids-file', default='')
    ap.add_argument('--dry-run', action='store_true', help='只统计，回滚全部修改')
    a = ap.parse_args()
    settings = PdfParseSettings.load(a.env)
    env = {**dotenv_values(BACKEND_ROOT / f'.env.{a.env}'), **os.environ}
    data_root = Path((env.get('LIT_DATA_ROOT') or '').strip('\'"'))
    conn = psycopg2.connect(db_dsn(a.env))
    total: Counter = Counter()
    try:
        with conn.cursor() as cur:
            if a.ids_file:
                ids = Path(a.ids_file).read_text(encoding='utf-8').split()
            else:
                cur.execute(
                    """SELECT r.source_id FROM lit_record r JOIN kb_document d ON d.record_id = r.record_id
                       JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current ORDER BY 1"""
                )
                ids = [r[0] for r in cur.fetchall()]
            for sid in ids:
                info = fix_one(cur, sid, data_root, settings)
                total.update({k: v for k, v in info.items() if isinstance(v, int) and not isinstance(v, bool)})
                print(f'{sid:>6} {info}')
        if a.dry_run:
            conn.rollback()
            print('（试运行，已回滚）')
        else:
            conn.commit()
    finally:
        conn.close()
    print('合计', dict(total))


if __name__ == '__main__':
    main()
