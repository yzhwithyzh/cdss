"""
概念归一：核心术语表导入、同义词归一化写法、候选概念自动合并、待审建议导出。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.terms import <核心术语表.xlsx>   # 导入/更新核心概念、同义词、上下位、疾病—症状草稿
    python -m tools.kb_pipeline.terms backfill                  # 为全部同义词写 alias_norm
    python -m tools.kb_pipeline.terms merge [--dry-run]         # 自动合并（只做确定无歧义的）
    python -m tools.kb_pipeline.terms suggest <输出.xlsx>        # 未合并候选 → 核心概念的向量相似建议，交人工审核
    python -m tools.kb_pipeline.terms apply <审核后.xlsx> --reviewer 姓名   # 执行审核表中“合并:…”的决定
    python -m tools.kb_pipeline.terms attach [--dry-run]       # 没有上级的候选概念 → 模型挂到核心概念下（待审）

自动合并只有两种：
1. 候选概念的写法（含去括注、括注缩写）归一化后，恰好等于一个核心概念的某个同义词 → 并入该核心概念；
2. 两个候选概念的写法归一化后完全相同 → 并为一个候选概念。
其余一律只给建议，由人工决定。合并时把所有引用改指目标概念，原写法转为目标的同义词，删除被合并的候选，
记入 kb_audit_event。
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import openpyxl
import psycopg2

from tools.kb_pipeline.ingest import db_dsn

ABBR = re.compile(r'^[A-Za-z][A-Za-z0-9\-/]{1,11}$')  # 括注中的缩写，如 (NBI)、(FIT)、(SSA/P)


def norm(text: str) -> str:
    """
    归一化写法：全半角、小写、连字符/斜杠/标点变空格、英文简单复数还原（全大写缩写如 APCS 不还原）
    """
    t = unicodedata.normalize('NFKC', text or '')
    t = re.sub(r"[\-‐‑‒–—−_/·,，、;；:：\"'“”‘’.。()（）\[\]]+", ' ', t)
    return ' '.join(_singular(w) for w in t.split())[:255]


def _singular(word: str) -> str:
    """
    小写 + 复数还原：polyps → polyp，lesions → lesion；全大写的缩写只转小写
    """
    if word.isupper() or not re.fullmatch(r'[A-Za-z]{4,}', word):
        return word.lower()
    w = word.lower()
    if w.endswith('ies'):
        return w[:-3] + 'y'
    if w.endswith('s') and not w.endswith(('ss', 'us', 'is')):
        return w[:-1]
    return w


def keys(text: str) -> set[str]:
    """
    一个写法可匹配的归一化键：全文、去掉括注的正文；括注在末尾且是缩写时，加上该缩写
    （“X (CTC) and Y”这类括注在中间的复合短语，不用缩写匹配）
    """
    out = {norm(text)}
    tail = re.search(r'[（(]([^()（）]+)[)）]\s*$', text or '')
    if tail and ABBR.match(tail.group(1).strip()):
        out.add(norm(tail.group(1).strip()))
    stripped = re.sub(r'\s*[（(][^()（）]*[)）]\s*', ' ', text or '').strip()
    if stripped:
        out.add(norm(stripped))
    return {k for k in out if k}


def lookup(cur: psycopg2.extensions.cursor, text: str) -> int | None:
    """
    原文写法 → 已有概念：按归一化键查同义词。命中唯一核心概念时用它；没有核心概念命中时，命中唯一候选概念才用；
    有歧义（命中多个）返回 None
    """
    cur.execute(
        """SELECT DISTINCT a.concept_id, c.status FROM kb_concept_alias a JOIN kb_concept c ON c.concept_id = a.concept_id
           WHERE a.alias_norm = ANY(%s) AND c.status <> 'deprecated'""",
        (list(keys(text)),),
    )
    rows = cur.fetchall()
    active = [cid for cid, status in rows if status == 'active']
    if active:
        return active[0] if len(set(active)) == 1 else None
    return rows[0][0] if len(rows) == 1 else None


# ---------------- 导入核心术语表 ----------------
def import_terms(cur: psycopg2.extensions.cursor, path: Path) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True)
    rows = [r for r in wb['概念'].iter_rows(min_row=2, values_only=True) if r and r[0]]
    cur.execute("SELECT topic_id FROM lit_topic WHERE topic_code = 'crc_adenoma'")
    topic_id = cur.fetchone()[0]
    ids: dict[str, int] = {}
    stats = defaultdict(int)
    for code, zh, en, ctype, _parent, _q, synonyms, _note in rows:
        cur.execute(
            """INSERT INTO kb_concept(topic_id, concept_type, code, name_zh, name_en, status)
               VALUES (%s, %s, %s, %s, %s, 'active')
               ON CONFLICT (code) DO UPDATE SET concept_type = EXCLUDED.concept_type, name_zh = EXCLUDED.name_zh,
                 name_en = EXCLUDED.name_en, status = 'active', record_version = kb_concept.record_version + 1
               RETURNING concept_id""",
            (topic_id, ctype, code, zh, en),
        )
        ids[code] = cur.fetchone()[0]
        names = [zh, en, *[s.strip() for s in re.split(r'[；;]', synonyms or '') if s.strip()]]
        cur.execute("DELETE FROM kb_concept_alias WHERE concept_id = %s AND source = 'standard'", (ids[code],))
        for name in dict.fromkeys(n for n in names if n):
            cur.execute(
                """INSERT INTO kb_concept_alias(concept_id, alias, lang, source, alias_norm)
                   VALUES (%s, %s, %s, 'standard', %s)
                   ON CONFLICT (concept_id, alias) DO UPDATE SET source = 'standard', alias_norm = EXCLUDED.alias_norm""",
                (ids[code], name[:255], 'zh' if re.search(r'[一-鿿]', name) else 'en', norm(name)),
            )
            stats['aliases'] += 1
    # 上下位：以表为准重建核心概念之间的 is_a
    cur.execute(
        """DELETE FROM kb_concept_relation WHERE rel_type = 'is_a' AND source = 'core_terms'
             AND child_id = ANY(%s)""",
        (list(ids.values()),),
    )
    for code, *_rest in rows:
        parent = _rest[3]
        if parent:
            if parent not in ids:
                raise ValueError(f'{code} 的上位编码 {parent} 不在表中')
            cur.execute(
                """INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source, review_status)
                   VALUES (%s, %s, 'is_a', 'core_terms', 'approved') ON CONFLICT DO NOTHING""",
                (ids[parent], ids[code]),
            )
            stats['is_a'] += 1
    for disease, symptom, note in wb['疾病-症状'].iter_rows(min_row=2, values_only=True):
        if disease and symptom:
            cur.execute(
                """INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source, review_status, note)
                   VALUES (%s, %s, 'has_symptom', 'manual', %s, %s)
                   ON CONFLICT (parent_id, child_id, rel_type)
                   DO UPDATE SET note = EXCLUDED.note, review_status = EXCLUDED.review_status""",
                (ids[disease], ids[symptom], 'approved' if '审核通过' in (note or '') else 'pending', note),
            )
            stats['has_symptom'] += 1
    # 同一归一化写法属于两个核心概念 → 术语表有歧义，需修改
    cur.execute(
        """SELECT a.alias_norm, array_agg(DISTINCT c.code) FROM kb_concept_alias a JOIN kb_concept c USING (concept_id)
           WHERE c.status = 'active' GROUP BY 1 HAVING count(DISTINCT c.concept_id) > 1"""
    )
    stats['ambiguous'] = cur.fetchall()
    stats['concepts'] = len(ids)
    return dict(stats)


def backfill(cur: psycopg2.extensions.cursor) -> int:
    cur.execute('SELECT alias_id, alias FROM kb_concept_alias')
    rows = cur.fetchall()
    for alias_id, alias in rows:
        cur.execute('UPDATE kb_concept_alias SET alias_norm = %s WHERE alias_id = %s', (norm(alias), alias_id))
    return len(rows)


# ---------------- 合并 ----------------
def merge_into(cur: psycopg2.extensions.cursor, src: int, dst: int, rule: str) -> None:
    from tools.kb_pipeline.load import (  # noqa: PLC0415  避免循环导入
        CONCEPT_ARRAY_REFERENCES,
        CONCEPT_JSONB_REFERENCES,
        CONCEPT_REFERENCES,
        write_outbox,
    )

    for table, col in CONCEPT_REFERENCES:
        if table == 'kb_concept_relation':
            continue
        cur.execute(f'UPDATE {table} SET {col} = %s WHERE {col} = %s', (dst, src))  # 表名列名来自常量
    for table, col in CONCEPT_ARRAY_REFERENCES:
        cur.execute(
            f"""UPDATE {table} SET {col} = ARRAY(SELECT DISTINCT unnest(array_replace({col}, %s, %s)))
                WHERE {col} @> ARRAY[%s::bigint]""",
            (src, dst, src),
        )
    # jsonb 中的概念引用（键 concept_id / *_concept_id）：jsonb 文本输出格式固定为 "key": value
    pattern = rf'("(?:[a-z_]*_)?concept_id"): {src}(?=[,}}\]])'
    for table, col in CONCEPT_JSONB_REFERENCES:
        cur.execute(
            f"""UPDATE {table} SET {col} = regexp_replace({col}::text, %s, %s, 'g')::jsonb
                WHERE {col}::text ~ %s""",
            (pattern, rf'\1: {dst}', pattern),
        )
    # 概念关系：主键含两端，先插入改指后的行再删原行
    cur.execute(
        """INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source, review_status, note)
           SELECT CASE WHEN parent_id = %(s)s THEN %(d)s ELSE parent_id END,
                  CASE WHEN child_id = %(s)s THEN %(d)s ELSE child_id END, rel_type, source, review_status, note
           FROM kb_concept_relation WHERE (parent_id = %(s)s OR child_id = %(s)s)
           ON CONFLICT DO NOTHING""",
        {'s': src, 'd': dst},
    )
    cur.execute(
        """UPDATE kb_span_link l SET entity_id = n.relation_id
           FROM kb_concept_relation o, kb_concept_relation n
           WHERE l.entity_type = 'concept_relation' AND l.entity_id = o.relation_id
             AND (o.parent_id = %(s)s OR o.child_id = %(s)s)
             AND n.rel_type = o.rel_type
             AND n.parent_id = CASE WHEN o.parent_id = %(s)s THEN %(d)s ELSE o.parent_id END
             AND n.child_id = CASE WHEN o.child_id = %(s)s THEN %(d)s ELSE o.child_id END""",
        {'s': src, 'd': dst},
    )
    cur.execute('DELETE FROM kb_concept_relation WHERE parent_id = %s OR child_id = %s', (src, src))
    cur.execute(
        """INSERT INTO kb_concept_alias(concept_id, alias, lang, source, mapping_note, alias_norm)
           SELECT %s, alias, lang, source, mapping_note, alias_norm FROM kb_concept_alias WHERE concept_id = %s
           ON CONFLICT (concept_id, alias) DO NOTHING""",
        (dst, src),
    )
    cur.execute('DELETE FROM kb_concept_alias WHERE concept_id = %s', (src,))
    cur.execute('DELETE FROM kb_concept WHERE concept_id = %s RETURNING uid, coalesce(name_zh, name_en), code', (src,))
    uid, name, code = cur.fetchone()
    cur.execute('SELECT code FROM kb_concept WHERE concept_id = %s', (dst,))
    cur.execute(
        """INSERT INTO kb_audit_event(entity_type, entity_id, action, changes, actor, reason)
           VALUES ('concept', %s, 'delete', %s, 'system:terms', %s)""",
        (src, json.dumps({'merged_into': cur.fetchone()[0], 'name': name, 'code': code}, ensure_ascii=False), rule),
    )
    write_outbox(cur, [('concept', str(uid), 'delete')])


def merge(cur: psycopg2.extensions.cursor) -> list[tuple[str, str, str]]:
    """
    :return: [(被合并的写法, 目标, 规则)]
    """
    done = []
    cur.execute(
        """SELECT c.concept_id, coalesce(c.name_zh, c.name_en), array_agg(a.alias) FROM kb_concept c
           LEFT JOIN kb_concept_alias a ON a.concept_id = c.concept_id
           WHERE c.status = 'candidate' GROUP BY 1, 2 ORDER BY 1"""
    )
    candidates = cur.fetchall()
    # 1. 候选 → 核心
    for cid, name, aliases in candidates:
        targets = set()
        for text in {name, *(a for a in aliases if a)}:
            cur.execute(
                """SELECT DISTINCT a.concept_id FROM kb_concept_alias a JOIN kb_concept c USING (concept_id)
                   WHERE c.status = 'active' AND a.alias_norm = ANY(%s)""",
                (list(keys(text)),),
            )
            targets |= {r[0] for r in cur.fetchall()}
        if len(targets) == 1:
            dst = targets.pop()
            cur.execute('SELECT coalesce(name_zh, name_en) FROM kb_concept WHERE concept_id = %s', (dst,))
            done.append((name, cur.fetchone()[0], 'exact_core_alias'))
            merge_into(cur, cid, dst, 'exact_core_alias')
    # 2. 候选之间写法相同
    cur.execute("SELECT concept_id, coalesce(name_zh, name_en) FROM kb_concept WHERE status = 'candidate' ORDER BY 1")
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cid, name in cur.fetchall():
        groups[norm(name)].append((cid, name))
    for members in groups.values():
        (keep, keep_name), *rest = members
        for cid, name in rest:
            done.append((name, keep_name, 'same_candidate_wording'))
            merge_into(cur, cid, keep, 'same_candidate_wording')
    return done


# ---------------- 执行审核结论 ----------------
def apply_review(cur: psycopg2.extensions.cursor, path: Path, reviewer: str) -> dict:
    """
    读取审核后的候选对应表：“决定”列为 合并:编码（并入核心概念）、合并:#候选ID（并入另一候选）或 保持候选
    """
    ws = openpyxl.load_workbook(path, read_only=True).active
    stats = defaultdict(int)
    for row in ws.iter_rows(min_row=2, values_only=True):
        cid, decision, reason = row[0], (row[7] or '').strip(), row[8]
        if not decision.startswith('合并:'):
            stats['keep'] += 1
            continue
        target = decision.split(':', 1)[1].strip()
        if target.startswith('#'):
            cur.execute('SELECT concept_id FROM kb_concept WHERE concept_id = %s', (int(target[1:]),))
        else:
            cur.execute("SELECT concept_id FROM kb_concept WHERE code = %s AND status = 'active'", (target,))
        dst = cur.fetchone()
        cur.execute('SELECT 1 FROM kb_concept WHERE concept_id = %s', (cid,))
        if not dst or not cur.fetchone():
            stats['skipped_missing'] += 1
            continue
        merge_into(cur, cid, dst[0], f'{reviewer}: {reason or decision}')
        stats['merged'] += 1
    return dict(stats)


# ---------------- 待审建议 ----------------
USED = """
SELECT c.concept_id, coalesce(c.name_zh, c.name_en), c.concept_type, count(*) AS uses FROM kb_concept c JOIN (
    SELECT subject_id AS cid FROM kb_edge UNION ALL SELECT object_id FROM kb_edge
    UNION ALL SELECT comparator_id FROM kb_edge UNION ALL SELECT unnest(population_ids) FROM kb_edge) r
    ON r.cid = c.concept_id
WHERE c.status = 'candidate' GROUP BY 1, 2, 3 ORDER BY uses DESC, 2"""


def suggest(cur: psycopg2.extensions.cursor, out: Path, top: int = 3) -> int:
    import numpy as np  # noqa: PLC0415

    from tools.kb_pipeline.embed import DEFAULT_MODEL, embed_batch, load_env  # noqa: PLC0415

    env = load_env('dev')
    cur.execute(
        """SELECT c.concept_id, c.code, c.name_zh, c.name_en, a.alias FROM kb_concept c
           JOIN kb_concept_alias a ON a.concept_id = c.concept_id WHERE c.status = 'active'"""
    )
    core_rows = cur.fetchall()
    cur.execute(USED)
    cands = cur.fetchall()

    def vectors(texts: list[str]) -> np.ndarray:
        out_vecs = []
        for i in range(0, len(texts), 10):
            out_vecs += embed_batch(env, DEFAULT_MODEL, 1024, texts[i : i + 10])[0]
        v = np.array(out_vecs, dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    core_vec = vectors([r[4] for r in core_rows])
    cand_vec = vectors([c[1] for c in cands])
    sims = cand_vec @ core_vec.T
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '候选对应'
    ws.append(
        [
            '候选ID',
            '候选写法',
            '类型',
            '使用次数',
            *[f'建议{i + 1}（编码｜名称｜相似度）' for i in range(top)],
            '决定（合并到编码 / 新建核心 / 保持候选 / 抽取错误）',
            '备注',
        ]
    )
    for (cid, name, ctype, uses), row in zip(cands, sims, strict=True):
        best: dict[int, tuple[float, int]] = {}
        for j in np.argsort(-row):
            concept = core_rows[j][0]
            if concept not in best:
                best[concept] = (float(row[j]), j)
            if len(best) == top:
                break
        sugg = [f'{core_rows[j][1]}｜{core_rows[j][2] or core_rows[j][3]}｜{s:.2f}' for s, j in best.values()]
        ws.append([cid, name, ctype, uses, *sugg, '', ''])
    for col, width in zip('ABCDEFGHI', (8, 50, 16, 8, 36, 36, 36, 30, 20), strict=True):
        ws.column_dimensions[col].width = width
    wb.save(out)
    return len(cands)


# ---------------- 候选概念挂上级 ----------------
ORPHANS = """
SELECT c.concept_id, coalesce(c.name_zh, c.name_en), c.concept_type FROM kb_concept c
WHERE c.status = 'candidate' AND NOT EXISTS (
    SELECT 1 FROM kb_concept_relation r WHERE r.child_id = c.concept_id AND r.rel_type = 'is_a'
      AND r.review_status <> 'rejected')
ORDER BY 1"""
ATTACH_PROMPT = """你是医学术语归类助手。每个候选概念给出了若干个已审核的核心概念（编码｜名称｜类型）。
对每个候选，判断它与哪个核心概念的关系：
- same：同一概念的不同写法（同义词、缩写、中英文）
- narrower：候选是该核心概念的一种（亚型、具体药物/器械/检查/术式、更具体的结局）
- 都不是：choice 填 null。以下情况一律填 null：
  · 候选是核心概念的属性或测量，而不是它的一种（“息肉大小”“腺瘤部位”“腺瘤数目”不是“息肉/腺瘤”的下位）；
  · 候选是两个以上概念的组合或并列（“X 联合 Y”“X 合并 Y”“X、Y 和 Z”）；
  · 候选与核心概念只是相关或同一领域（“胶囊内镜”不是“结肠镜检查”的一种）；
  · 不确定。宁可不挂，不要勉强归类。
只输出 JSON：{"results":[{"id":候选ID,"choice":"核心编码或null","relation":"same|narrower|null"}]}"""
ATTACH_BATCH = 20


def attach(cur: psycopg2.extensions.cursor, env_name: str, top: int = 8) -> dict:
    """
    没有上级的候选概念 → 向量取最相近的 top 个核心概念 → 模型判定同义/下位/无关 →
    写 kb_concept_relation is_a（source=auto_parent，review_status=pending，note=same/narrower），待人工审核。
    按 is_a 展开检索时，这些候选就能被它的上级概念查到。
    """
    import numpy as np  # noqa: PLC0415
    import openai  # noqa: PLC0415

    from tools.kb_pipeline.embed import DEFAULT_MODEL, embed_batch, load_env  # noqa: PLC0415
    from tools.kb_pipeline.extract import LlmSettings  # noqa: PLC0415

    cur.execute(ORPHANS)
    orphans = cur.fetchall()
    cur.execute(
        """SELECT concept_id, code, concat_ws(' / ', name_zh, name_en), concept_type FROM kb_concept
           WHERE status = 'active' ORDER BY 1"""
    )
    core = cur.fetchall()
    if not orphans or not core:
        return {'orphans': len(orphans), 'attached': 0}
    env = load_env(env_name)

    def vectors(texts: list[str]) -> np.ndarray:
        with ThreadPoolExecutor(max_workers=4) as ex:
            parts = ex.map(lambda i: embed_batch(env, DEFAULT_MODEL, 1024, texts[i : i + 10])[0], range(0, len(texts), 10))
            v = np.array([vec for part in parts for vec in part], dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    sims = vectors([o[1] for o in orphans]) @ vectors([c[2] for c in core]).T
    by_code = {c[1]: c[0] for c in core}
    settings = LlmSettings.load(env_name)
    client = openai.OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout)
    stats: dict = defaultdict(int, orphans=len(orphans))

    def ask(start: int) -> tuple[dict[int, set[str]], list[dict] | None]:
        lines, allowed = [], {}
        for (cid, name, ctype), row in zip(
            orphans[start : start + ATTACH_BATCH], sims[start : start + ATTACH_BATCH], strict=True
        ):
            options = [core[j] for j in np.argsort(-row)[:top]]
            allowed[cid] = {o[1] for o in options}
            opts = '；'.join(f'{o[1]}｜{o[2]}｜{o[3]}' for o in options)
            lines.append(f'候选 {cid}：{name}（{ctype}）\n  可选：{opts}')
        kwargs: dict = {
            'model': settings.model,
            'temperature': 0,
            'messages': [{'role': 'system', 'content': ATTACH_PROMPT}, {'role': 'user', 'content': '\n'.join(lines)}],
            'extra_body': {'thinking': {'type': 'disabled'}},
        }
        if settings.json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        try:
            reply = client.chat.completions.create(**kwargs).choices[0].message.content or '{}'
            return allowed, json.loads(reply[reply.find('{') : reply.rfind('}') + 1]).get('results') or []
        except (openai.OpenAIError, json.JSONDecodeError) as e:
            print(f'  第 {start // ATTACH_BATCH + 1} 批失败：{e}', file=sys.stderr)
            return allowed, None

    with ThreadPoolExecutor(max_workers=settings.concurrency) as ex:
        replies = list(ex.map(ask, range(0, len(orphans), ATTACH_BATCH)))
    for allowed, results in replies:  # 写库在主线程顺序进行
        if results is None:
            stats['batch_failed'] += 1
            continue
        for r in results:
            cid, choice, relation = r.get('id'), r.get('choice'), r.get('relation')
            if cid not in allowed:
                continue
            if choice not in allowed[cid] or relation not in ('same', 'narrower'):
                stats['no_parent'] += 1
                continue
            cur.execute(
                """INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source, review_status, note)
                   VALUES (%s, %s, 'is_a', 'auto_parent', 'pending', %s) ON CONFLICT DO NOTHING""",
                (by_code[choice], cid, relation),
            )
            stats[relation] += 1
    return dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description='概念归一')
    ap.add_argument('command', choices=('import', 'backfill', 'merge', 'suggest', 'apply', 'attach'))
    ap.add_argument('path', nargs='?')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--reviewer', default='reviewer', help='apply：审核人，记入审计')
    a = ap.parse_args()
    conn = psycopg2.connect(db_dsn(a.env))
    try:
        with conn.cursor() as cur:
            if a.command == 'import':
                print(import_terms(cur, Path(a.path)))
            elif a.command == 'backfill':
                print('alias_norm 回填', backfill(cur))
            elif a.command == 'merge':
                done = merge(cur)
                for src, dst, rule in done:
                    print(f'  {rule:24} {src[:60]!r} → {dst[:40]!r}')
                print('合并', len(done))
            elif a.command == 'apply':
                print(apply_review(cur, Path(a.path), a.reviewer))
            elif a.command == 'attach':
                print(attach(cur, a.env))
            else:
                print('导出候选', suggest(cur, Path(a.path or '候选对应建议.xlsx')))
        if a.dry_run:
            conn.rollback()
            print('（试运行，已回滚）', file=sys.stderr)
        else:
            conn.commit()
    finally:
        conn.close()


if __name__ == '__main__':
    main()
