"""
40 篇结构化文献评测（评测集/kb40_eval_v0.1.json，108 题）。

第一层（--layer 1）：每个检索工具单独测能否找到金标准
  lit       search_literature（模型改写英文检索词）前 20 条里有没有这篇
  passage   search_passages（问句原文，向量）前 6 段里有没有这篇；P 题另看是否命中那一段
  struct    F 题：search_findings 前 8 条里有没有那条研究发现；G 题：search_recommendations 前 8 条里有没有那条推荐
证据可见性（--layer 3）：全文段落检索返回给模型的文字里，是否包含答案原句（F/G/M 看原句开头 40 字，P 看金标准段落是否在内）。
  对比不同的返回条数 k 和每段字数上限（只取开头 / 取检索词最密的一截）
第二层（--layer 2）：智能体端到端
  --graph 按位打开图检索工具：1=概念关系汇总（explore_concept），2=按条件查关系表（query_evidence），3=都开，--data agg 跑跨文献汇总题（评测集/kb40_agg_v0.1.json，H 型）
  H 型另看 gold_doc_recall：金标准文献中被回答引用的比例；判分按“覆盖了多少篇、内容是否与依据一致”
  doc_retrieved / doc_cited / fabricated / used_struct（是否对这篇用过结构化工具）/ judge（对照答案要点和原文判分）

用法（ruoyi-fastapi-backend 目录下）：
    python -m tests.plugin_kbweb.eval_kb40 --layer 1 --out ../../_bmad-output/kb40_layer1.json
    python -m tests.plugin_kbweb.eval_kb40 --layer 2 --out ../../_bmad-output/kb40_layer2.json
"""

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plugins.kbweb.agent import retrieval as R
from plugins.kbweb.agent.agent import fmt_passages
from plugins.kbweb.agent.llm import chat_model, rewrite_query
from plugins.kbweb.agent.runner import run_events

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / '评测集' / 'kb40_eval_v0.1.json'
AGG = ROOT / '评测集' / 'kb40_agg_v0.1.json'
CACHE = ROOT / '_bmad-output' / 'kb40_rewrites.json'
DSN = 'postgresql+asyncpg://postgres:123456@127.0.0.1:5432/cdss'
_cache: dict = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.is_file() else {}

JUDGE = """你在核对一个问答系统的回答是否答对了。下面给出问题、标准答案要点和原文依据。
评分：2=要点全部答对（数字一致，允许单位/写法差异）；1=部分要点对或数字有出入；0=答错、没答到要点，或称"库内查不到"。
只输出 JSON：{"score":0|1|2,"reason":"一句话"}

问题：{q}
标准答案要点：{facts}
原文依据：{quote}

系统回答：
{answer}"""


JUDGE_AGG = """你在核对一个问答系统对“跨文献汇总”问题的回答。下面给出问题、库内应当覆盖的各篇文献及其要点。
评分：2=覆盖了大部分（约 80% 以上）文献且所述内容与要点一致；1=覆盖了一部分文献，或有内容与要点不一致；0=只覆盖极少、内容明显错误或称查不到。
只输出 JSON：{"score":0|1|2,"reason":"一句话"}

问题：{q}
应覆盖的文献及要点：
{facts}

系统回答：
{answer}"""


async def rewritten(q: str) -> list[str]:
    if q not in _cache:
        _cache[q] = await rewrite_query(q)
        CACHE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding='utf-8')
    return _cache[q]


def ratio(a, b):
    return round(a / b, 3) if b else None


async def layer1(factory, items):
    rows = []
    for it in items:
        kws = await rewritten(it['question'])
        async with factory() as db:
            lit = [r['source_id'] for r in await R.search_literature(db, kws, limit=20)]
            psg = await R.search_passages(db, it['question'], k=6)
            row = {'qid': it['qid'], 'type': it['type'], 'lit': it['source_id'] in lit,
                   'passage': it['source_id'] in {p['source_id'] for p in psg}}
            if it['type'] == 'P':
                row['block'] = it['block_id'] in {p['block_id'] for p in psg}
            if it['type'] == 'F':
                row['struct'] = it['finding_id'] in {f['finding_id'] for f in await R.search_structured(db, kws, 'finding')}
            if it['type'] == 'G':
                row['struct'] = it['rec_id'] in {r['rec_id'] for r in await R.search_structured(db, kws, 'recommendation')}
        rows.append(row)
    summary = {}
    for g in ['all', 'F', 'G', 'M', 'P']:
        rs = [r for r in rows if g == 'all' or r['type'] == g]
        summary[g] = {'n': len(rs), 'lit@20': ratio(sum(r['lit'] for r in rs), len(rs)), 'passage@6': ratio(sum(r['passage'] for r in rs), len(rs)),
                      'block@6': ratio(sum(r.get('block', False) for r in rs), sum('block' in r for r in rs)),
                      'struct@8': ratio(sum(r.get('struct', False) for r in rs), sum('struct' in r for r in rs)),
                      'any': ratio(sum(r['lit'] or r['passage'] or r.get('struct', False) for r in rs), len(rs))}
    return {'summary': summary, 'rows': rows}


def _norm(t: str) -> str:
    return re.sub(r'\s+', ' ', t or '').strip().lower()


async def layer3(factory, items):
    variants = {'k6_head700': (6, 700, True), 'k6_win700': (6, 700, False), 'k6_win2000': (6, 2000, False),
                'k8_win2000': (8, 2000, False), 'k10_win2000': (10, 2000, False)}
    rows = []
    for it in items:
        kws = await rewritten(it['question'])
        async with factory() as db:
            psg = await R.search_passages(db, it['question'], k=10)
        anchor = _norm(it['gold_quote'].split('…')[0])[:40]
        row = {'qid': it['qid'], 'type': it['type']}
        for name, (k, limit, head) in variants.items():
            top = psg[:k]
            if it['type'] == 'P':
                row[name] = it['block_id'] in {p['block_id'] for p in top}
            else:
                shown = '\n'.join(p['text'][:limit] for p in top) if head else fmt_passages(top, kws + [it['question']], limit)
                row[name] = anchor in _norm(shown)
        rows.append(row)
    summary = {g: {'n': len(rs), **{v: ratio(sum(r[v] for r in rs), len(rs)) for v in variants}}
               for g in ['all', 'F', 'G', 'M', 'P'] for rs in [[r for r in rows if g == 'all' or r['type'] == g]]}
    return {'summary': summary, 'rows': rows}


async def judge(it, answer):
    prompt = JUDGE_AGG if it['type'] == 'H' else JUDGE
    facts = '\n'.join(it['key_facts']) if it['type'] == 'H' else ('；'.join(it['key_facts']) or '（见原文依据）')
    for k, v in {'q': it['question'], 'facts': facts[:6000], 'quote': it['gold_quote'][:1500],
                 'answer': answer or '（无回答）'}.items():
        prompt = prompt.replace('{' + k + '}', v)
    for attempt in range(3):  # 网络偶发断连时重试，不让一题拖垮整轮评测
        try:
            msg = await chat_model(0.0).ainvoke(prompt)
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return {'score': None, 'reason': f'评分调用失败：{e}'}
            await asyncio.sleep(5 * (attempt + 1))
    m = re.search(r'\{.*\}', str(msg.content), re.S)
    try:
        return json.loads(m.group(0))
    except (AttributeError, json.JSONDecodeError):
        return {'score': None, 'reason': '评分解析失败'}


async def run_one(factory, it, graph):
    steps, retrieved, answer, cites, secs = [], set(), '', [], 0.0
    struct_hit = False
    try:
        async for ev in run_events(factory, it['question'], graph=graph):
            if ev['type'] == 'step':
                steps.append({'tool': ev['tool'], 'detail': ev['detail']})
            elif ev['type'] == 'tool_result':
                ids = set(re.findall(r'【(\d+)】', ev['content']))
                retrieved |= ids
                if ev['tool'] in ('search_findings', 'search_recommendations', 'get_record', 'explore_concept') and \
                        (it.get('source_id') in ids or set(it.get('gold_docs', [])) & ids):
                    struct_hit = True
            elif ev['type'] == 'answer':
                answer, secs = ev['content'], ev['t']
            elif ev['type'] == 'citations':
                cites = ev['items']
    except Exception as e:  # noqa: BLE001
        answer = f'[运行失败] {e}'
    cited = {c['source_id'] for c in cites}
    gold = set(it.get('gold_docs') or [it['source_id']])
    return {'qid': it['qid'], 'type': it['type'], 'source_id': it.get('source_id'), 'question': it['question'],
            'gold_doc_recall': round(len(gold & cited) / len(gold), 3), 'gold_doc_retrieved': round(len(gold & retrieved) / len(gold), 3),
            'doc_retrieved': bool(gold & retrieved), 'doc_cited': bool(gold & cited), 'used_struct': struct_hit,
            'used_graph': 'explore_concept' in [s['tool'] for s in steps],
            'used_edge': 'query_evidence' in [s['tool'] for s in steps],
            'fabricated': sorted(c['source_id'] for c in cites if not c['retrieved']), 'tools': [s['tool'] for s in steps],
            'seconds': secs, 'judge': await judge(it, answer), 'answer': answer}


async def layer2(factory, items, concurrency, graph):
    sem = asyncio.Semaphore(concurrency)

    async def guarded(it):
        async with sem:
            r = await run_one(factory, it, graph)
            print(f"{r['qid']:5} recall={r['gold_doc_recall']} ret={'Y' if r['doc_retrieved'] else '-'} cit={'Y' if r['doc_cited'] else '-'} "
                  f"struct={'Y' if r['used_struct'] else '-'} judge={r['judge'].get('score')} {r['seconds']}s", flush=True)
            return r

    rows = await asyncio.gather(*(guarded(it) for it in items))
    summary = {}
    for g in ['all', 'F', 'G', 'M', 'P', 'H']:
        rs = [r for r in rows if g == 'all' or r['type'] == g]
        if not rs:
            continue
        sc = [r['judge'].get('score') for r in rs if r['judge'].get('score') is not None]
        summary[g] = {'n': len(rs), 'doc_retrieved': ratio(sum(r['doc_retrieved'] for r in rs), len(rs)),
                      'doc_cited': ratio(sum(r['doc_cited'] for r in rs), len(rs)), 'used_struct': ratio(sum(r['used_struct'] for r in rs), len(rs)),
                      'fabricated': sum(1 for r in rs if r['fabricated']),
                      'gold_doc_recall': round(sum(r['gold_doc_recall'] for r in rs) / len(rs), 3),
                      'gold_doc_retrieved': round(sum(r['gold_doc_retrieved'] for r in rs) / len(rs), 3),
                      'used_graph': ratio(sum(r['used_graph'] for r in rs), len(rs)),
                      'used_edge': ratio(sum(r['used_edge'] for r in rs), len(rs)),
                      'judge_mean': round(sum(sc) / len(sc), 2) if sc else None,
                      'correct(2)': sum(1 for s in sc if s == 2), 'partial(1)': sum(1 for s in sc if s == 1), 'wrong(0)': sum(1 for s in sc if s == 0),
                      'avg_seconds': round(sum(r['seconds'] for r in rs) / len(rs), 1)}
    return {'summary': summary, 'rows': rows}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--layer', type=int, default=1)
    ap.add_argument('--types', default='FGMP')
    ap.add_argument('--concurrency', type=int, default=4)
    ap.add_argument('--out')
    ap.add_argument('--graph', type=int, default=0)
    ap.add_argument('--data', default='kb40', choices=['kb40', 'agg'])
    a = ap.parse_args()
    src = AGG if a.data == 'agg' else DATA
    items = [it for it in json.loads(src.read_text(encoding='utf-8'))['items'] if a.data == 'agg' or it['type'] in a.types]
    engine = create_async_engine(DSN, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rep = await {1: lambda: layer1(factory, items), 2: lambda: layer2(factory, items, a.concurrency, a.graph), 3: lambda: layer3(factory, items)}[a.layer]()
    await engine.dispose()
    if a.out:
        Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(rep['summary'], ensure_ascii=False, indent=1))


if __name__ == '__main__':
    asyncio.run(main())
