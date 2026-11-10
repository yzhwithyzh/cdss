"""
门诊症状→鉴别诊断：受控词表入库（疾病清单 + 症状体征词表），以及库内症状候选的归一处理。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.dx_vocab load <门诊症状鉴别诊断_范围_v0.2_AI审核demo.xlsx> [--dry-run]
    python -m tools.kb_pipeline.dx_vocab apply-candidates [--dry-run]

load：每个词条先按中英文名、同义词查已有概念（terms.lookup）——
  命中核心概念：保留原编码，补同义词；命中候选概念：升为核心概念（编码、名称、类型按词表）；未命中：新建。
  ICD-10 编码写入 kb_concept_code_map（status=candidate，AI 预填待核对）；便血、黑便、呕血 IS_A 消化道出血。
apply-candidates：按审核表“库内候选归一建议”处理剩余候选（合并 / 上下位 / 弃用 / 关系改指）。
当前审核由 AI 完成（demo），审计记录 actor=reviewer:claude-demo，正式使用前需临床重审。
"""

import argparse
import json
import re
import sys
from pathlib import Path

import openpyxl
import psycopg2

from tools.kb_pipeline.ingest import db_dsn
from tools.kb_pipeline.load import write_outbox
from tools.kb_pipeline.terms import lookup, merge_into, norm

ACTOR = 'reviewer:claude-demo'
NOTE = 'AI 审核（demo），非临床'
TOPIC_ID = 1
TYPES = {'疾病清单': 'disease_or_lesion', '症状体征词表': 'symptom_or_sign'}
BLEEDING_CHILDREN = ['SYM:HEMATOCHEZIA', 'SYM:MELENA', 'SYM:HEMATEMESIS']

# 审核表“库内候选归一建议”的处理（loader 已把能按名称命中的候选升为核心概念，这里只处理剩下的）
CANDIDATE_ACTIONS = {
    4951: ('merge', 'SYM:OBSTRUCTION', 'signs or symptoms of obstruction 与“肠梗阻表现”同义'),
    4860: ('is_a', 'SYM:ABDOMINAL_PAIN', '更具体：腹痛 + 性质：阵发性'),
    5002: ('is_a', 'SYM:INCONTINENCE', '更具体：大便失禁 + 程度：部分'),
    1270: ('is_a', 'SYM:BLOATING', '更具体：腹胀 + 情境：术后；程度：轻微'),
    4862: ('deprecate', None, '复合写法：便血 + 黑便，两者鉴别方向不同，不整体合并'),
    4724: ('deprecate', None, '复合写法，已拆分为单个症状'),
    4725: ('deprecate', None, '复合写法，已拆分为单个症状'),
    4727: ('deprecate', None, '复合写法，已拆分为单个症状'),
    4865: ('deprecate', None, '复合写法，已拆分为单个症状'),
    4953: ('deprecate', None, '非症状（疾病间关系）'),
    'SRUS_PROLAPSE': ('repoint', 'SYM:ANAL_PROTRUSION', 'SRUS 表现为“直肠脱垂”改指症状“肛门肿物脱出”；直肠脱垂本身为疾病'),
}


def split_names(*fields: str) -> list[str]:
    out = []
    for f in fields:
        out += [x.strip() for x in re.split(r'[、；;]| / ', f or '') if x.strip()]
    return list(dict.fromkeys(out))


def icd_codes(text: str) -> list[str]:
    t = re.sub(r'（[^）]*）', '', text or '')
    return [x.strip() for x in re.split(r'[、；;/]', t) if x.strip()]


def audit(cur, entity_type: str, entity_id: int, action: str, changes: dict, reason: str) -> None:
    cur.execute(
        """INSERT INTO kb_audit_event(entity_type, entity_id, action, changes, actor, reason)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (entity_type, entity_id, action, json.dumps(changes, ensure_ascii=False), ACTOR, reason),
    )


def read_rows(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = []
    for sheet, ctype in TYPES.items():
        it = wb[sheet].iter_rows(values_only=True)
        header = next(it)
        for r in it:
            if r and r[0]:
                d = dict(zip(header, r, strict=False))
                rows.append({'type': ctype, 'code': d['编码'], 'zh': d.get('中文名') or d.get('中文标准名'),
                             'en': d['英文名'], 'syn': d.get('同义词') or '', 'icd10': d.get('ICD-10（待核对）') or ''})
    return rows


def load(cur, path: Path) -> dict:
    stats = {'created': 0, 'promoted': 0, 'kept': 0, 'aliases': 0, 'codes': 0, 'conflicts': []}
    claimed: dict[int, str] = {}  # 本次已被某个词条认领的概念：别的词条只因共用同义词命中它时不能再认领
    cur.execute('SELECT concept_id, code FROM kb_concept WHERE code = ANY(%s)', ([r['code'] for r in read_rows(path)],))
    by_code = dict((code, cid) for cid, code in cur.fetchall())
    for row in read_rows(path):
        names = split_names(row['zh'], row['en'], row['syn'])
        hits = {n: lookup(cur, n) for n in names}
        if row['code'] in by_code:  # 重复运行：按编码直接定位
            hits = {'__code__': by_code[row['code']], **hits}
        found = [cid for cid in hits.values() if cid and claimed.get(cid, row['code']) == row['code']]
        clashes = {n: c for n, c in hits.items() if c and claimed.get(c, row['code']) != row['code']}
        if clashes or len(set(found)) > 1:
            stats['conflicts'].append((row['code'], {n: c for n, c in hits.items() if c}))
        cid = found[0] if found else None
        if cid is None:
            cur.execute("""INSERT INTO kb_concept(topic_id, concept_type, code, name_zh, name_en, status)
                           VALUES (%s, %s, %s, %s, %s, 'active') RETURNING concept_id, uid""",
                        (TOPIC_ID, row['type'], row['code'], row['zh'], row['en']))
            cid, uid = cur.fetchone()
            stats['created'] += 1
            audit(cur, 'concept', cid, 'create', {'code': row['code']}, f'门诊鉴别诊断词表；{NOTE}')
        else:
            cur.execute('SELECT status, code, uid FROM kb_concept WHERE concept_id = %s', (cid,))
            status, code, uid = cur.fetchone()
            if status == 'candidate':
                cur.execute("""UPDATE kb_concept SET status = 'active', code = %s, name_zh = %s, name_en = %s, concept_type = %s,
                               record_version = record_version + 1 WHERE concept_id = %s""",
                            (row['code'], row['zh'], row['en'], row['type'], cid))
                stats['promoted'] += 1
                audit(cur, 'concept', cid, 'update', {'from_code': code, 'to_code': row['code'], 'status': 'candidate→active'},
                      f'候选升为核心概念（门诊鉴别诊断词表）；{NOTE}')
            else:
                stats['kept'] += 1
        claimed[cid] = row['code']
        for n in names:
            if clashes.get(n):  # 这个写法已属于别的词条，不重复挂
                continue
            lang = 'zh' if re.search(r'[一-鿿]', n) else 'en'
            cur.execute("""INSERT INTO kb_concept_alias(concept_id, alias, lang, source, mapping_note, alias_norm)
                           VALUES (%s, %s, %s, 'dx_vocab', %s, %s) ON CONFLICT (concept_id, alias) DO NOTHING""",
                        (cid, n, lang, NOTE, norm(n)))
            stats['aliases'] += cur.rowcount
        for code in icd_codes(row['icd10']):
            cur.execute("""INSERT INTO kb_concept_code_map(concept_id, code_system, code, system_version, map_type, status, source)
                           VALUES (%s, 'ICD-10', %s, '国家临床版（待核对）', 'exact', 'candidate', 'AI预填（demo）')
                           ON CONFLICT DO NOTHING""", (cid, code))
            stats['codes'] += cur.rowcount
        write_outbox(cur, [('concept', str(uid), 'upsert')])
    # 消化道出血的下位
    cur.execute("SELECT concept_id FROM kb_concept WHERE code = 'SYM:GI_BLEEDING'")
    parent = cur.fetchone()[0]
    for code in BLEEDING_CHILDREN:
        cur.execute("""INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source, review_status, note)
                       SELECT %s, concept_id, 'is_a', 'dx_vocab', 'approved', %s FROM kb_concept WHERE code = %s
                       ON CONFLICT DO NOTHING""", (parent, NOTE, code))
    return stats


def concept_by_code(cur, code: str) -> int:
    cur.execute('SELECT concept_id FROM kb_concept WHERE code = %s', (code,))
    return cur.fetchone()[0]


def apply_candidates(cur) -> list[str]:
    log = []
    for key, (action, target, reason) in CANDIDATE_ACTIONS.items():
        if action == 'repoint':
            # SRUS has_symptom 直肠脱垂 → has_symptom 肛门肿物脱出
            cur.execute("""SELECT r.relation_id, r.parent_id, r.child_id FROM kb_concept_relation r
                           JOIN kb_concept c ON c.concept_id = r.child_id
                           WHERE r.rel_type = 'has_symptom' AND c.code = 'DX:RECTAL_PROLAPSE'""")
            for rid, parent, child in cur.fetchall():
                new_child = concept_by_code(cur, target)
                cur.execute("""UPDATE kb_concept_relation SET child_id = %s, note = coalesce(note, '') || %s
                               WHERE relation_id = %s""", (new_child, f'；{reason}（{NOTE}）', rid))
                audit(cur, 'concept_relation', rid, 'update', {'child_from': child, 'child_to': new_child}, reason)
                log.append(f'关系 {rid}: 改指 {target}')
            continue
        cur.execute('SELECT status, coalesce(name_zh, name_en) FROM kb_concept WHERE concept_id = %s', (key,))
        row = cur.fetchone()
        if not row:
            log.append(f'{key}: 已不存在，跳过')
            continue
        status, name = row
        if action == 'merge':
            dst = concept_by_code(cur, target)
            if dst != key:
                merge_into(cur, key, dst, f'{reason}；{NOTE}')
                log.append(f'{key} {name}: 并入 {target}')
        elif action == 'is_a':
            parent = concept_by_code(cur, target)
            cur.execute("""INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source, review_status, note)
                           VALUES (%s, %s, 'is_a', 'dx_vocab', 'approved', %s) ON CONFLICT DO NOTHING""",
                        (parent, key, f'{reason}；{NOTE}'))
            log.append(f'{key} {name}: IS_A {target}')
        elif action == 'deprecate' and status != 'deprecated':
            cur.execute("UPDATE kb_concept SET status = 'deprecated', record_version = record_version + 1 WHERE concept_id = %s", (key,))
            audit(cur, 'concept', key, 'update', {'status': f'{status}→deprecated'}, f'{reason}；{NOTE}')
            log.append(f'{key} {name}: 弃用')
    return log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['load', 'apply-candidates'])
    ap.add_argument('path', nargs='?')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    with psycopg2.connect(db_dsn(a.env)) as conn, conn.cursor() as cur:
        out = load(cur, Path(a.path)) if a.cmd == 'load' else apply_candidates(cur)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        if a.dry_run:
            conn.rollback()
            print('dry-run：已回滚')
        else:
            conn.commit()


if __name__ == '__main__':
    sys.exit(main())
