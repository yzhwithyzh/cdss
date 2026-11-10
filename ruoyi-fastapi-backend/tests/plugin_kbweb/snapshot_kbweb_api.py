"""
kbweb 接口快照：40 篇结构化文献的详情页 + 若干检索/概念/统计接口，按条目数汇总，存 JSON。
用于 PICO 表 → kb_edge 迁移前后对比（数量应一致；ID 会变，不比对 ID）。

    python -m tests.plugin_kbweb.snapshot_kbweb_api --out ../../_bmad-output/kbweb_snapshot_before.json
    python -m tests.plugin_kbweb.snapshot_kbweb_api --compare before.json after.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from plugins.kbweb.dao.kbweb_dao import KbwebDao

PAGE = {'page_num': 1, 'page_size': 500}


def summarize_record(b: dict) -> dict:
    kb = b.get('kb') or {}
    fs = kb.get('findings') or []
    recs = kb.get('recommendations') or []
    return {
        'findings': len(fs),
        'results': sum(len(f['results']) for f in fs),
        'finding_spans': sum(len(f['spans']) for f in fs),
        'populations': len(kb.get('populations') or []),
        'conditions': sum(len(p['conditions']) for p in kb.get('populations') or []),
        'arms': len(kb.get('arms') or []),
        'interventions': sum(len(a['interventions']) for a in kb.get('arms') or []),
        'outcomes': len(kb.get('outcomes') or []),
        'assertions': len(kb.get('assertions') or []),
        'definitions': len(kb.get('definitions') or []),
        'recs': len(recs),
        'rec_conditions': sum(len(r['conditions']) for r in recs),
        'rec_actions': sum(len(r['actions']) for r in recs),
        'rec_citations': sum(len(r['citations']) for r in recs),
        'concepts': len(kb.get('concepts') or {}),
        'cited_by': len(b.get('cited_by') or []),
        'exposure_labels': sorted({f['exposure_as_reported'] or '' for f in fs}),
        'outcome_labels': sorted({f['outcome_label'] or '' for f in fs}),
    }


async def snapshot() -> dict:
    eng = create_async_engine('postgresql+asyncpg://postgres:123456@localhost/cdss')
    out: dict = {'records': {}}
    async with AsyncSession(eng) as db:
        rids = (await db.execute(text('SELECT r.record_id, r.source_id FROM kb_document d JOIN lit_record r USING (record_id) ORDER BY 1'))).all()
        for rid, sid in rids:
            out['records'][sid] = summarize_record(await KbwebDao.record_bundle(db, rid))
        s = await KbwebDao.stats(db)
        out['stats'] = {k: s['counts'][k] for k in ('findings', 'results', 'recommendations', 'studies')}
        f = await KbwebDao.search_findings(db, {**PAGE, 'measure': 'or'})
        out['search_findings_or'] = {'total': f['total'], 'facets': {k: len(v) for k, v in f['facets'].items()}}
        f = await KbwebDao.search_findings(db, {**PAGE, 'q': 'aspirin'})
        out['search_findings_aspirin'] = f['total']
        r = await KbwebDao.search_recommendations(db, {**PAGE})
        out['search_recs'] = {'total': r['total'], 'action_type': sorted((x['key'], x['n']) for x in r['facets']['action_type'])}
        cid = (await db.execute(text("SELECT concept_id FROM kb_concept WHERE code = 'RISK:ASPIRIN'"))).scalar()
        c = await KbwebDao.concept_bundle(db, cid)
        out['concept_aspirin'] = {k: len(c[k]) for k in ('findings', 'recommendations', 'assertions', 'populations', 'records')}
        cmp_ = await KbwebDao.compare(db, None, None)
        out['compare'] = {'recs': len(cmp_['recommendations']), 'matrix_cells': len(cmp_['matrix'])}
        q = await KbwebDao.question_bundle(db, 'Q1')
        out['question_q1'] = {'findings': len(q['findings']), 'structured_findings': sum(x['findings'] for x in q['structured'])}
    await eng.dispose()
    return out


def compare(a: dict, b: dict) -> int:
    diffs = 0
    for sid in sorted(set(a['records']) | set(b['records'])):
        x, y = a['records'].get(sid, {}), b['records'].get(sid, {})
        d = {k: (x.get(k), y.get(k)) for k in set(x) | set(y) if x.get(k) != y.get(k)}
        if d:
            diffs += 1
            print(sid, {k: v if not isinstance(v[0], list) else f'{len(v[0] or [])}→{len(v[1] or [])} 项不同' for k, v in d.items()})
    for k in a:
        if k != 'records' and a[k] != b.get(k):
            diffs += 1
            print(k, a[k], '→', b.get(k))
    print('不一致', diffs)
    return diffs


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--compare', nargs=2)
    a = ap.parse_args()
    if a.compare:
        sys.exit(1 if compare(*(json.loads(Path(p).read_text(encoding='utf-8')) for p in a.compare)) else 0)
    snap = asyncio.run(snapshot())
    Path(a.out).write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in snap.items() if k != 'records'}, ensure_ascii=False))
