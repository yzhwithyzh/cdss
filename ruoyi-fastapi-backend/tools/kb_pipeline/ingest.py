"""
原文层入库：lit_record → kb_document / kb_document_version / kb_block，并写图同步事件

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.ingest --env dev --ids 2800,41115,7358
    python -m tools.kb_pipeline.ingest --env dev --ids-file ids.txt
同一文件（sha256 相同）重复入库时会替换该版本的全部解析块（幂等）。
"""

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path

import psycopg2
from dotenv import dotenv_values
from psycopg2.extras import execute_values

from tools.kb_pipeline.blocks import (
    KEEP_REFERENCE_DESIGNS,
    Block,
    blocks_from_jats,
    blocks_from_mineru,
    body_language,
    prepare_blocks,
    search_text,
)
from tools.pdf_parsing.config import BACKEND_ROOT, PdfParseSettings
from tools.pdf_parsing.text_layer import assess_text_layer

PARSER_VERSION = 'mineru-v4-pipeline'


def db_dsn(env_name: str) -> str:
    env: dict = {}
    for name in (f'.env.{env_name}', f'.env.{env_name}.local'):
        if (BACKEND_ROOT / name).is_file():
            env.update(dotenv_values(BACKEND_ROOT / name))
    env.update(os.environ)
    sources = json.loads(env['DB_SOURCES'])
    db = sources[(env.get('DB_DEFAULT_SOURCE') or 'primary').strip('\'"')]
    return (
        f'host={db["db_host"]} port={db["db_port"]} dbname={db["db_database"]} '
        f'user={db["db_username"]} password={db["db_password"]}'
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def locate_sources(source_id: str, data_root: Path, settings: PdfParseSettings) -> dict | None:
    """
    找到该文献的全文与解析结果：优先 XML（结构化），否则 PDF + MinerU 输出
    """
    xml = data_root / 'pdf' / 'xml_fulltext' / f'{source_id}.xml'
    if xml.is_file():
        return {'format': 'xml', 'file': xml, 'parse_method': 'xml', 'blocks': blocks_from_jats(xml)}
    pdf = data_root / 'pdf' / f'{source_id}.pdf'
    out = settings.output_dir / 'mineru' / settings.mineru_model / source_id
    lists = glob.glob(str(out / '*_content_list.json'))
    if not pdf.is_file() or not lists:
        return None
    report = assess_text_layer(pdf)
    content = json.loads(Path(lists[0]).read_text(encoding='utf-8'))
    import pymupdf  # noqa: PLC0415

    with pymupdf.open(str(pdf)) as doc:
        rotation = doc[0].rotation if doc.page_count else 0
    return {
        'format': 'pdf',
        'file': pdf,
        'parse_method': 'text' if report.verdict == 'ok' else 'ocr',
        'text_layer_ok': report.verdict == 'ok',
        'pages': report.pages,
        'rotation': rotation,
        'blocks': blocks_from_mineru(content),
    }


def keeps_references(cur: psycopg2.extensions.cursor, record_id: int) -> bool:
    """
    指南、共识、系统综述保留参考文献：按筛选阶段的研究类型，或已有抽取结果的文献类型判断
    """
    cur.execute(
        """SELECT EXISTS (SELECT 1 FROM lit_study_design s WHERE s.record_id = %s AND s.is_current
                          AND s.design_label = ANY(%s))
               OR EXISTS (SELECT 1 FROM kb_document d WHERE d.record_id = %s
                          AND d.doc_kind IN ('guideline', 'consensus_or_position', 'systematic_review_or_ma'))""",
        (record_id, list(KEEP_REFERENCE_DESIGNS), record_id),
    )
    return cur.fetchone()[0]


def ingest_one(cur: psycopg2.extensions.cursor, record: tuple, src: dict, data_root: Path) -> dict:
    record_id, topic_id, source_id, title = record
    cur.execute('SELECT document_id FROM kb_document WHERE record_id = %s', (record_id,))
    row = cur.fetchone()
    if row:
        document_id = row[0]
    else:
        cur.execute(
            'INSERT INTO kb_document(topic_id, record_id) VALUES (%s, %s) RETURNING document_id', (topic_id, record_id)
        )
        document_id = cur.fetchone()[0]
    digest = sha256(src['file'])
    rel_path = str(src['file'].relative_to(data_root)).replace('\\', '/')
    parser_version = PARSER_VERSION if src['format'] == 'pdf' else 'jats-xml'
    # 换文件或换解析器版本时新增版本行；旧版本保留并标为非当前，已有引用不失效
    cur.execute(
        """UPDATE kb_document_version SET is_current = false
           WHERE document_id = %s AND is_current AND NOT (file_sha256 = %s AND parser_version = %s)""",
        (document_id, digest, parser_version),
    )
    cur.execute(
        """INSERT INTO kb_document_version(document_id, file_sha256, source_format, file_path, parse_method,
                                           parser_version, text_layer_ok, page_count)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (document_id, file_sha256, parser_version) DO UPDATE
             SET parse_method = EXCLUDED.parse_method, text_layer_ok = EXCLUDED.text_layer_ok,
                 page_count = EXCLUDED.page_count, is_current = true
           RETURNING version_id, uid""",
        (
            document_id,
            digest,
            src['format'],
            rel_path,
            src['parse_method'],
            parser_version,
            src.get('text_layer_ok'),
            src.get('pages'),
        ),
    )
    version_id, _ = cur.fetchone()
    # 幂等：替换该版本的解析块（已被原文片段引用时不允许替换）
    cur.execute('SELECT count(*) FROM kb_span WHERE version_id = %s', (version_id,))
    if cur.fetchone()[0]:
        raise RuntimeError(f'{source_id}: 该版本已有原文片段引用，不能替换解析块')
    cur.execute('DELETE FROM kb_block WHERE version_id = %s', (version_id,))
    blocks: list[Block] = prepare_blocks(src['blocks'], keeps_references(cur, record_id))
    execute_values(
        cur,
        """INSERT INTO kb_block(version_id, block_key, seq, page, page_end, section, block_type, heading_level,
                               table_ref, text, search_text, bbox, extra)
           VALUES %s""",
        [
            (
                version_id,
                b.block_key,
                b.seq,
                b.page,
                b.page_end,
                b.section,
                b.block_type,
                b.heading_level,
                b.table_ref,
                b.text,
                search_text(b.text),
                b.bbox_json(src.get('rotation', 0)),
                json.dumps(b.extra, ensure_ascii=False) if b.extra else None,
            )
            for b in blocks
        ],
    )
    cur.execute(
        'UPDATE kb_document SET body_language = %s, record_version = record_version + 1, update_time = now() '
        'WHERE document_id = %s RETURNING uid, record_version, doc_kind',
        (body_language(blocks), document_id),
    )
    doc_uid, doc_rv, doc_kind = cur.fetchone()
    cur.execute(
        """INSERT INTO kb_graph_outbox(entity_type, entity_uid, record_version, op, payload)
           VALUES ('document', %s, %s, 'upsert', %s) ON CONFLICT DO NOTHING""",
        (
            doc_uid,
            doc_rv,
            json.dumps(
                {
                    'label': 'Document',
                    'props': {'record_id': record_id, 'source_id': source_id, 'title': title, 'doc_kind': doc_kind},
                },
                ensure_ascii=False,
            ),
        ),
    )
    sections: dict[str, int] = {}
    for b in blocks:
        sections[b.section] = sections.get(b.section, 0) + 1
    return {
        'source_id': source_id,
        'format': src['format'],
        'parse_method': src['parse_method'],
        'blocks': len(blocks),
        'tables': sum(b.block_type == 'table' for b in blocks),
        'multi_region': sum(len(b.regions) > 1 for b in blocks),
        'sections': sections,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description='原文层入库')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--ids', default='', help='逗号分隔的题录 ID（lit_record.source_id）')
    ap.add_argument('--ids-file', default='')
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(',') if x.strip()]
    if a.ids_file:
        ids += Path(a.ids_file).read_text(encoding='utf-8').split()
    settings = PdfParseSettings.load(a.env)
    env = {**dotenv_values(BACKEND_ROOT / f'.env.{a.env}'), **os.environ}
    data_root = Path((env.get('LIT_DATA_ROOT') or '').strip('\'"'))
    conn = psycopg2.connect(db_dsn(a.env))
    done, missing = [], []
    try:
        for sid in ids:
            with conn, conn.cursor() as cur:  # 每篇一个事务
                cur.execute('SELECT record_id, topic_id, source_id, title FROM lit_record WHERE source_id = %s', (sid,))
                record = cur.fetchone()
                src = locate_sources(sid, data_root, settings) if record else None
                if not src:
                    missing.append(sid)
                    continue
                info = ingest_one(cur, record, src, data_root)
                done.append(info)
                print(
                    f'{sid:>6} {info["format"]}/{info["parse_method"]:4} 块 {info["blocks"]:>4}  表格 {info["tables"]:>2}'
                    f'  多区域段落 {info["multi_region"]:>3}  {info["sections"]}'
                )
    finally:
        conn.close()
    print(f'完成 {len(done)} 篇；缺少全文或解析结果 {len(missing)} 篇 {missing}')


if __name__ == '__main__':
    main()
