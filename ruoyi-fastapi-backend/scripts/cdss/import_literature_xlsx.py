"""
将题录 Excel 导入文献表（lit_record / lit_study_design / lit_pico）。

用法（在 ruoyi-fastapi-backend 目录下）：
    .venv/Scripts/python.exe scripts/cdss/import_literature_xlsx.py \
        --xlsx "E:/code/结直肠腺瘤项目/结直肠腺瘤-26.10.4.xlsx" \
        --topic-code crc_adenoma --topic-name 结直肠腺瘤 \
        --matched "E:/code/结直肠腺瘤项目/matched.json" \
        --pdf-dir "E:/code/结直肠腺瘤项目/pdf" \
        --title-fixes "E:/code/结直肠腺瘤项目/title_fixes_结直肠腺瘤-26.10.4.json"

同一病种下同一文件（按 SHA-256 判断）只允许导入一次；整个导入在一个事务内完成。
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import dotenv_values
from psycopg2.extras import execute_values

COLUMNS = {
    'ID': 'source_id',
    'Title': 'title',
    'Authors': 'authors',
    'Year': 'year',
    'Abstract': 'abstract',
    'Participants (LLM_A)': 'participants',
    'Intervention (LLM_A)': 'intervention',
    'Comparison (LLM_A)': 'comparison',
    'Outcomes (LLM_A)': 'outcomes',
    'Study design': 'study_design',
}
# 来源文件中表示“缺失”的占位文本，入库时存为空
PLACEHOLDERS = {
    '',
    'no abstract found',
    'no year found',
    'no authors found',
    'not reported',
    '未报告',
    'nan',
    'none',
    '空',
}
CJK = re.compile(r'[\u4e00-\u9fff]')
OPERATOR = 'import'


def clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in PLACEHOLDERS else text


def clean_source_db(value: object) -> str | None:
    """来源库以“/”分隔，去掉其中表示未知的“空”。"""
    parts = [part for part in (clean(item) for item in str(value or '').split('/')) if part]
    return '/'.join(parts) or None


def same_title(a: str, b: str) -> bool:
    """忽略大小写与空白差异（含窄空格等 Unicode 空白）比较标题。"""
    return re.sub(r'\s+', '', a).lower() == re.sub(r'\s+', '', b).lower()


def parse_year(value: object) -> int | None:
    text = clean(value)
    return int(text) if text and re.fullmatch(r'\d{4}', text) else None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def connect(env_file: Path) -> 'psycopg2.extensions.connection':
    sources = json.loads(dotenv_values(env_file)['DB_SOURCES'])
    db = sources['primary']
    if db['db_type'] != 'postgresql':
        sys.exit(f'仅支持 PostgreSQL，当前配置为 {db["db_type"]}')
    return psycopg2.connect(
        host=db['db_host'],
        port=db['db_port'],
        user=db['db_username'],
        password=db['db_password'],
        dbname=db['db_database'],
    )


def main() -> None:  # noqa: PLR0912, PLR0915
    parser = argparse.ArgumentParser(description='导入题录 Excel 到文献表')
    parser.add_argument('--xlsx', required=True, type=Path)
    parser.add_argument('--sheet', default=0, help='工作表名称或序号，默认第一个')
    parser.add_argument('--topic-code', required=True)
    parser.add_argument('--topic-name', required=True)
    parser.add_argument('--matched', type=Path, help='matched.json，用于补充 DOI/PMID/来源库（按 ID 对应）')
    parser.add_argument('--pdf-dir', type=Path, help='全文目录，文件名为 <ID>.pdf')
    parser.add_argument(
        '--title-fixes', type=Path, help='补充标题 JSON：{ID: {title, pmid, evidence}}，仅用于来源标题为空的行'
    )
    parser.add_argument('--design-source', default='LLM_A', help='研究类型与 PICO 的来源标识')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[2] / '.env.dev')
    args = parser.parse_args()

    sheet = int(args.sheet) if str(args.sheet).isdigit() else args.sheet
    sheet_name = pd.ExcelFile(args.xlsx).sheet_names[sheet] if isinstance(sheet, int) else sheet
    df = pd.read_excel(args.xlsx, sheet_name=sheet_name, dtype=str)
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        sys.exit(f'缺少列：{sorted(missing)}')
    df = df.rename(columns=COLUMNS)
    df['source_row'] = df.index + 2  # Excel 行号（含表头）

    matched = {}
    if args.matched:
        matched = {str(r['id']): r for r in json.loads(args.matched.read_text(encoding='utf-8'))}
    pdf_ids = set()
    if args.pdf_dir:
        pdf_ids = {p.stem for p in args.pdf_dir.glob('*.pdf')}
    title_fixes = {}
    if args.title_fixes:
        title_fixes = json.loads(args.title_fixes.read_text(encoding='utf-8'))

    file_hash = sha256_of(args.xlsx)
    design_source = f'{args.design_source}（{args.xlsx.name}）'
    log_lines = []
    seen = {}
    records, designs, picos = [], [], []

    for row in df.to_dict('records'):
        source_id = clean(row['source_id'])
        title = clean(row['title'])
        remark = None
        fix = title_fixes.get(source_id) if source_id else None
        if source_id and not title and fix:
            title = fix['title']
            remark = f'标题补充：{fix.get("evidence") or args.title_fixes.name}'
            log_lines.append(f'第{row["source_row"]}行：ID {source_id} 标题为空，已用补充文件填写')
        if not source_id or not title:
            log_lines.append(f'第{row["source_row"]}行：缺少ID或标题，跳过')
            continue
        if source_id in seen:
            first = seen[source_id]
            same = same_title(first['title'], title)
            log_lines.append(
                f'第{row["source_row"]}行：ID {source_id} 与第{first["source_row"]}行重复'
                f'（{"标题仅大小写或空白不同" if same else "标题不同"}），保留首行'
            )
            continue
        seen[source_id] = {'title': title, 'source_row': row['source_row']}

        meta = matched.get(source_id, {})
        if meta and (meta.get('title') or '').strip().lower()[:40] != title.lower()[:40]:
            log_lines.append(f'ID {source_id}：matched.json 标题不一致，未采用其 DOI/PMID')
            meta = {}
        records.append(
            {
                'source_id': source_id,
                'source_row': row['source_row'],
                'title': title,
                'authors': clean(row['authors']),
                'pub_year': parse_year(row['year']),
                'abstract': clean(row['abstract']),
                'language': 'zh' if CJK.search(title) else 'en',
                'doi': clean(meta.get('doi')),
                'pmid': clean(meta.get('pmid')) or (clean(fix.get('pmid')) if remark else None),
                'source_db': clean_source_db(meta.get('src')),
                'pdf_path': f'pdf/{source_id}.pdf' if source_id in pdf_ids else None,
                'remark': remark,
            }
        )
        label = clean(row['study_design'])
        if label:
            designs.append((source_id, label))
        pico = [clean(row[k]) for k in ('participants', 'intervention', 'comparison', 'outcomes')]
        if any(pico):
            picos.append((source_id, *pico))

    conn = connect(args.env_file)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """insert into lit_topic (topic_code, topic_name, create_by, create_time)
                   values (%s, %s, %s, now())
                   on conflict (topic_code) do update set topic_code = excluded.topic_code
                   returning topic_id""",
                (args.topic_code, args.topic_name, OPERATOR),
            )
            topic_id = cur.fetchone()[0]
            cur.execute(
                'select batch_id from lit_import_batch where topic_id = %s and file_sha256 = %s',
                (topic_id, file_hash),
            )
            if cur.fetchone():
                sys.exit('该文件已导入过此病种，未做任何修改。')

            cur.execute(
                """insert into lit_import_batch
                   (topic_id, source_file, source_sheet, file_sha256, total_rows, imported_rows,
                    skipped_rows, import_log, create_by, create_time)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now()) returning batch_id""",
                (
                    topic_id,
                    args.xlsx.name,
                    sheet_name,
                    file_hash,
                    len(df),
                    len(records),
                    len(df) - len(records),
                    '\n'.join(log_lines) or None,
                    OPERATOR,
                ),
            )
            batch_id = cur.fetchone()[0]

            id_map = dict(
                execute_values(
                    cur,
                    """insert into lit_record
                       (topic_id, batch_id, source_id, source_row, title, authors, pub_year, abstract,
                        language, doi, pmid, source_db, pdf_path, remark, create_by, create_time)
                       values %s returning source_id, record_id""",
                    [
                        (
                            topic_id,
                            batch_id,
                            r['source_id'],
                            r['source_row'],
                            r['title'],
                            r['authors'],
                            r['pub_year'],
                            r['abstract'],
                            r['language'],
                            r['doi'],
                            r['pmid'],
                            r['source_db'],
                            r['pdf_path'],
                            r['remark'],
                            OPERATOR,
                        )
                        for r in records
                    ],
                    template='(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())',
                    page_size=2000,
                    fetch=True,
                )
            )
            # 新导入的结果成为当前结果；同一题录此前的当前结果（如有）先置为历史
            cur.execute(
                'update lit_study_design set is_current = false where is_current and record_id = any(%s)',
                ([id_map[s] for s, _ in designs],),
            )
            execute_values(
                cur,
                """insert into lit_study_design
                   (record_id, design_label, method, source, is_current, review_status, create_by, create_time)
                   values %s""",
                [(id_map[s], label, 'import', design_source, True, '0', OPERATOR) for s, label in designs],
                template='(%s, %s, %s, %s, %s, %s, %s, now())',
                page_size=2000,
            )
            execute_values(
                cur,
                """insert into lit_pico
                   (record_id, participants, intervention, comparison, outcomes, method, source,
                    create_by, create_time)
                   values %s""",
                [(id_map[s], p, i, c, o, 'import', design_source, OPERATOR) for s, p, i, c, o in picos],
                template='(%s, %s, %s, %s, %s, %s, %s, %s, now())',
                page_size=2000,
            )
    finally:
        conn.close()

    print(f'病种：{args.topic_name}（topic_id={topic_id}），批次：{batch_id}')
    print(f'来源行数 {len(df)}，导入题录 {len(records)}，研究类型 {len(designs)}，PICO {len(picos)}')
    print(f'有全文 {sum(1 for r in records if r["pdf_path"])}，有DOI {sum(1 for r in records if r["doi"])}')
    for line in log_lines:
        print('  -', line)


if __name__ == '__main__':
    main()
