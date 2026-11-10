"""
症状归一评测（评测集/symptom_norm_eval_v0.1.json，AI 标注 demo）。

术语题指标：
  accuracy   预测的编码集合与期望完全一致
  wrong_map  错合率：预测里出现了期望之外的编码（含“不应映射却映射了”）——上线门槛看这一项
  missed     漏归率：期望的编码没有预测出来
  attr_ok    带修饰语的题：主症状对且属性里保留了修饰信息
  exact_wrong 走精确匹配却合错的条数（应为 0）
描述题指标：（编码, 状态）对的精确率 / 召回率；只看编码时的召回率

用法（ruoyi-fastapi-backend 目录下）：
    python -m tests.plugin_kbweb.eval_symptom_norm --out ../../_bmad-output/symptom_norm_v1.json
"""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tools.kb_pipeline.normalize import SymptomNormalizer

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / '评测集' / 'symptom_norm_eval_v0.1.json'
DSN = 'postgresql+asyncpg://postgres:123456@127.0.0.1:5432/cdss'


def r3(a, b):
    return round(a / b, 3) if b else None


async def run_terms(nz, items, sem):
    async def one(it):
        async with sem:
            try:
                ms = await nz.normalize_term(it['input'])
            except Exception as e:  # noqa: BLE001  一题失败不拖垮整轮；记为答错并保留原因
                exp = set(it['expect'])
                return {**it, 'pred': [], 'error': f'{type(e).__name__}: {e}'[:200], 'maps': [], 'ok': not exp, 'wrong': False,
                        'missed': bool(exp), 'exact_wrong': False, **({'attr_ok': False} if 'attr' in it else {})}
        pred = sorted({m.code for m in ms if m.code})
        exp = set(it['expect'])
        row = {**it, 'pred': pred, 'maps': [{'code': m.code, 'rel': m.relation, 'method': m.method, 'attrs': m.attributes,
                                             'reason': m.reason} for m in ms],
               'ok': set(pred) == exp, 'wrong': bool(set(pred) - exp), 'missed': bool(exp - set(pred)),
               'exact_wrong': any(m.method == 'exact' and m.code and m.code not in exp for m in ms)}
        if 'attr' in it:
            row['attr_ok'] = row['ok'] and any(it['attr'] in json.dumps(m.attributes, ensure_ascii=False) or it['attr'] in m.text
                                               and m.relation == 'NARROWER' for m in ms)
        return row
    return await asyncio.gather(*(one(it) for it in items))


async def run_texts(nz, items, sem):
    async def one(it):
        async with sem:
            try:
                ms = await nz.normalize_text(it['input'])
            except Exception as e:  # noqa: BLE001
                return {**it, 'pred': [], 'error': str(e)[:200]}
        return {**it, 'pred': sorted({(m.code, m.status) for m in ms if m.code})}
    return await asyncio.gather(*(one(it) for it in items))


def summarize(terms, texts):
    by = defaultdict(list)
    for r in terms:
        by['all'].append(r); by[r['cat']].append(r)
    s = {k: {'n': len(v), 'accuracy': r3(sum(r['ok'] for r in v), len(v)), 'wrong_map': r3(sum(r['wrong'] for r in v), len(v)),
             'missed': r3(sum(r['missed'] for r in v), len(v)),
             **({'attr_ok': r3(sum(r['attr_ok'] for r in v if 'attr_ok' in r), sum('attr_ok' in r for r in v))} if any('attr_ok' in r for r in v) else {})}
         for k, v in by.items()}
    s['all']['exact_used'] = r3(sum(any(m['method'] == 'exact' for m in r['maps']) for r in terms), len(terms))
    s['all']['exact_wrong'] = sum(r['exact_wrong'] for r in terms)
    tp = sum(len({tuple(x) for x in t['expect']} & {tuple(x) for x in t['pred']}) for t in texts)
    npred = sum(len(t['pred']) for t in texts)
    nexp = sum(len(t['expect']) for t in texts)
    code_tp = sum(len({x[0] for x in t['expect']} & {x[0] for x in t['pred']}) for t in texts)
    s['texts'] = {'n': len(texts), 'pair_precision': r3(tp, npred), 'pair_recall': r3(tp, nexp), 'code_recall': r3(code_tp, nexp),
                  'exact_cases': r3(sum({tuple(x) for x in t['expect']} == {tuple(x) for x in t['pred']} for t in texts), len(texts))}
    return s


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out')
    ap.add_argument('--concurrency', type=int, default=6)
    a = ap.parse_args()
    data = json.loads(DATA.read_text(encoding='utf-8'))
    engine = create_async_engine(DSN)
    async with async_sessionmaker(engine)() as db:
        nz = await SymptomNormalizer.load(db)
    await engine.dispose()
    sem = asyncio.Semaphore(a.concurrency)
    terms = await run_terms(nz, data['terms'], sem)
    texts = await run_texts(nz, data['texts'], sem)
    rep = {'summary': summarize(terms, texts), 'terms': terms, 'texts': texts}
    if a.out:
        Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=list), encoding='utf-8')
    print(json.dumps(rep['summary'], ensure_ascii=False, indent=1))
    errs = [(r['id'], r['error']) for r in terms if r.get('error')] + [(t['id'], t['error']) for t in texts if t.get('error')]
    if errs:
        print('运行出错：', errs[:10])
    for r in terms:
        if not r['ok']:
            print('✗', r['id'], r['cat'], r['input'], 'expect', r['expect'], 'pred', r['pred'], '|', '; '.join(f"{m['rel']}:{m['reason'][:40]}" for m in r['maps']))
    for t in texts:
        if {tuple(x) for x in t['expect']} != {tuple(x) for x in t['pred']}:
            print('✗', t['id'], t['input'][:30], 'expect', t['expect'], 'pred', t['pred'])


if __name__ == '__main__':
    asyncio.run(main())
