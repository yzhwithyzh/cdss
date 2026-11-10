"""
第一层评测：检索召回（不调大模型）。

T1 文献召回：评测集/clinical_eval_v0.1.json 中带“必中文献”的问题，检索全部题录，统计必中文献在前 10 / 20 的召回率，
   按题型、临床问题分组。两种检索词来源：
   - zh：问句分词 + 概念别名扩展（不调模型的下限）
   - llm：大模型把问句改写成英文检索词（智能体实际的检索方式）
T2 段落召回：_parsed/embed_eval/questions.json（40 题，金标准为 block_id），统计 R@5 / R@10，
   对比 向量 / 关键词（模型改写词）/ 融合 三种方式。
模型改写结果缓存在 _bmad-output/eval_rewrites_v2.json，重复评测不重复调用。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tests.plugin_kbweb.eval_retrieval [--t1] [--t2] [--out report.json]
"""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plugins.kbweb.agent import retrieval as R
from plugins.kbweb.agent.llm import rewrite_query

ROOT = Path(__file__).resolve().parents[4]
CLINICAL = ROOT / '评测集' / 'clinical_eval_v0.1.json'
PASSAGE = ROOT / '_parsed' / 'embed_eval' / 'questions.json'
DSN = 'postgresql+asyncpg://postgres:123456@127.0.0.1:5432/cdss'
CACHE = ROOT / '_bmad-output' / 'eval_rewrites_v2.json'  # 改写提示词变了就换文件名
_cache: dict = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.is_file() else {}


async def rewritten(q: str) -> list[str]:
    if q not in _cache:
        _cache[q] = await rewrite_query(q)
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding='utf-8')
    return _cache[q]


def ratio(a: int, b: int) -> float:
    return round(a / b, 3) if b else 0.0


async def t1(db, mode: str) -> dict:
    qs = [q for q in json.loads(CLINICAL.read_text(encoding='utf-8'))['questions'] if q['must_list']]
    agg = defaultdict(lambda: [0, 0, 0])  # 分组 → [必中总数, @10 命中, @20 命中]
    misses = []
    for q in qs:
        # zh：不调模型的下限；llm：只用模型改写出的英文词（混入中文词会让召回减半，见 v2 对比）
        kws = R.tokenize(q['q']) + await R.expand_terms(db, q['q']) if mode == 'zh' else await rewritten(q['q'])
        top = [r['source_id'] for r in await R.search_literature(db, kws, limit=20)]
        for m in q['must_list']:
            h10, h20 = m in top[:10], m in top
            for key in ('all', 'type:' + q['type'], 'eq:' + q['eq']):
                agg[key][0] += 1; agg[key][1] += h10; agg[key][2] += h20
            if not h20:
                misses.append({'qid': q['qid'], 'must': m, 'keywords': kws[:12]})
    return {
        'questions': len(qs),
        'recall': {k: {'n': v[0], 'R@10': ratio(v[1], v[0]), 'R@20': ratio(v[2], v[0])} for k, v in sorted(agg.items())},
        'misses': misses,
    }


async def t2(db) -> dict:
    qs = json.loads(PASSAGE.read_text(encoding='utf-8'))
    res = {}
    for mode in ('vector', 'keyword', 'hybrid'):
        h5 = h10 = 0
        for q in qs:
            if mode == 'vector':
                rows = await R.search_passages(db, q['question'], k=10)
            elif mode == 'keyword':
                rows = await R.search_passages(db, q['question'], await rewritten(q['question']), k=10, use_vector=False)
            else:
                rows = await R.search_passages(db, q['question'], await rewritten(q['question']), k=10, use_keyword=True)
            ids = [r['block_id'] for r in rows]
            h5 += q['block_id'] in ids[:5]; h10 += q['block_id'] in ids
        res[mode] = {'n': len(qs), 'R@5': ratio(h5, len(qs)), 'R@10': ratio(h10, len(qs))}
    return res


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--t1', action='store_true'); ap.add_argument('--t2', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    run_all = not (a.t1 or a.t2)
    engine = create_async_engine(DSN)
    report = {}
    async with async_sessionmaker(engine)() as db:
        if a.t1 or run_all:
            report['T1_literature_zh'] = await t1(db, 'zh')
            report['T1_literature_llm'] = await t1(db, 'llm')
        if a.t2 or run_all:
            report['T2_passage'] = await t2(db)
    await engine.dispose()
    s = json.dumps(report, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(s, encoding='utf-8')
    brief = {k: ({g: r for g, r in v['recall'].items() if g == 'all' or g.startswith('type:')} if k.startswith('T1') else v) for k, v in report.items()}
    print(json.dumps(brief, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
