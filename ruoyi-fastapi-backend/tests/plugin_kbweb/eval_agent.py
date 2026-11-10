"""
第二层评测：问答智能体端到端（调大模型，较慢）。

每题记录：
- must_retrieved  必中文献是否出现在任一工具结果里（检索层面）
- must_cited      必中文献是否被回答引用（回答层面）
- fabricated      引用了工具从未返回过的 source_id（编造引用）
- skill_ok        是否读取了该题型应读的技能
- tool_errors     工具调用报错次数
- judge           大模型按参考答案 / 注意事项 / 典型错误打分：2 正确完整，1 部分正确，0 错误或犯了典型错误

用法（ruoyi-fastapi-backend 目录下）：
    python -m tests.plugin_kbweb.eval_agent --per-type 2 --out ../../_bmad-output/eval_agent_v1.json
    python -m tests.plugin_kbweb.eval_agent --qids Q1-01,Q2-03
"""

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plugins.kbweb.agent.llm import chat_model
from plugins.kbweb.agent.runner import run_events

ROOT = Path(__file__).resolve().parents[4]
CLINICAL = ROOT / '评测集' / 'clinical_eval_v0.1.json'
DSN = 'postgresql+asyncpg://postgres:123456@127.0.0.1:5432/cdss'
# 技能有交叉：比较的是随访间隔时读 surveillance-interval 也对；术语版本问题在适用性清单里也有
EXPECTED_SKILL = {'R': {'surveillance-interval'}, 'E': {'evidence-search'}, 'S': {'evidence-search'}, 'A': {'applicability-check'},
                  'C': {'guideline-compare', 'surveillance-interval'}, 'D': {'guideline-compare', 'surveillance-interval', 'evidence-search'},
                  'V': {'guideline-compare', 'applicability-check'}}

JUDGE = """你是消化内镜专家，评估一个问答系统的回答。参考答案中标【待原文核对】的指南条款视为正确的参考。
评分：2=结论与参考一致、关键注意事项都提到；1=方向对但缺关键分歧/前提/注意事项；0=结论错误，或犯了“典型错误”。
不要因为引用编号不同扣分，只看临床内容。只输出 JSON：{"score":0|1|2,"hit_caveat":true/false,"made_wrong":true/false,"reason":"一句话"}

问题：{q}
参考答案：{ans}
必须注意：{caveat}
典型错误：{wrong}

系统回答：
{answer}"""


async def judge(q: dict, answer: str) -> dict:
    # 模板里有 JSON 花括号，不能用 str.format
    prompt = JUDGE
    for k, v in {'q': q['q'], 'ans': q['ans'], 'caveat': q['caveat'] or '无', 'wrong': q['wrong'] or '无', 'answer': answer or '（无回答）'}.items():
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
        return json.loads(m.group(0)) if m else {'score': None, 'reason': '评分解析失败'}
    except json.JSONDecodeError:
        return {'score': None, 'reason': '评分解析失败'}


async def run_one(factory, q: dict, graph: int = 0) -> dict:
    steps, retrieved, errors, answer, cites, elapsed = [], set(), 0, '', [], 0.0
    try:
        async for ev in run_events(factory, q['q'], graph=graph):
            if ev['type'] == 'step':
                steps.append({'tool': ev['tool'], 'detail': ev['detail']})
            elif ev['type'] == 'tool_result':
                retrieved.update(re.findall(r'【(\d+)】', ev['content']))
                errors += ev['error']
            elif ev['type'] == 'answer':
                answer, elapsed = ev['content'], ev['t']
            elif ev['type'] == 'citations':
                cites = ev['items']
    except Exception as e:  # noqa: BLE001
        answer = f'[运行失败] {e}'
    cited = {c['source_id'] for c in cites}
    must = set(q['must_list'])
    skills = {s['detail'] for s in steps if s['tool'] == 'read_file'}
    return {
        'qid': q['qid'], 'type': q['type'], 'eq': q['eq'], 'q': q['q'],
        'must': sorted(must), 'must_retrieved': sorted(must & retrieved), 'must_cited': sorted(must & cited),
        'ok_cited': sorted(set(q['ok_list']) & cited),
        'fabricated': sorted(c['source_id'] for c in cites if not c['retrieved']),
        'skill_ok': bool(EXPECTED_SKILL[q['type']] & skills), 'skills': sorted(skills),
        'tool_calls': len(steps), 'tool_errors': errors, 'seconds': elapsed,
        'judge': await judge(q, answer), 'answer': answer, 'steps': steps,
    }


def summarize(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups['all'].append(r); groups['type:' + r['type']].append(r)

    def agg(rs):
        must = sum(len(r['must']) for r in rs)
        scores = [r['judge'].get('score') for r in rs if r['judge'].get('score') is not None]
        return {
            'n': len(rs),
            'must_retrieved': round(sum(len(r['must_retrieved']) for r in rs) / must, 3) if must else None,
            'must_cited': round(sum(len(r['must_cited']) for r in rs) / must, 3) if must else None,
            'fabricated_answers': sum(1 for r in rs if r['fabricated']),
            'skill_ok': round(sum(r['skill_ok'] for r in rs) / len(rs), 3),
            'tool_errors': sum(r['tool_errors'] for r in rs),
            'judge_mean': round(sum(scores) / len(scores), 2) if scores else None,
            'judge_2': sum(1 for s in scores if s == 2), 'judge_0': sum(1 for s in scores if s == 0),
            'avg_seconds': round(sum(r['seconds'] for r in rs) / len(rs), 1),
        }
    return {k: agg(v) for k, v in sorted(groups.items())}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--qids', default='')
    ap.add_argument('--per-type', type=int, default=2)
    ap.add_argument('--all', action='store_true', help='跑全部 91 题')
    ap.add_argument('--concurrency', type=int, default=3)
    ap.add_argument('--out', default=None)
    ap.add_argument('--graph', type=int, default=0, help='按位：1=explore_concept，2=query_evidence')
    a = ap.parse_args()
    qs = json.loads(CLINICAL.read_text(encoding='utf-8'))['questions']
    if a.all:
        pass
    elif a.qids:
        want = set(a.qids.split(','))
        qs = [q for q in qs if q['qid'] in want]
    else:
        # 每个题型取前 N 道（固定抽样，便于前后版本对比）
        by = defaultdict(list)
        for q in qs:
            by[q['type']].append(q)
        qs = [q for t in sorted(by) for q in by[t][:a.per_type]]
    engine = create_async_engine(DSN, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sem = asyncio.Semaphore(a.concurrency)

    async def guarded(q):
        async with sem:
            r = await run_one(factory, q, a.graph)
            print(f"{r['qid']:7} {r['type']} judge={r['judge'].get('score')} must {len(r['must_retrieved'])}/{len(r['must_cited'])}/{len(r['must'])} "
                  f"skill={'Y' if r['skill_ok'] else 'N'} err={r['tool_errors']} fab={len(r['fabricated'])} {r['seconds']}s", flush=True)
            return r

    rows = await asyncio.gather(*(guarded(q) for q in qs))
    await engine.dispose()
    report = {'summary': summarize(rows), 'rows': rows}
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=1))


if __name__ == '__main__':
    asyncio.run(main())
