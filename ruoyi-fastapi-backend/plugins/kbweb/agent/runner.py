"""
跑一轮问答并把过程整理成事件流：工具调用 → 工具结果 → 最终回答 → 引用核对。
接口（SSE）和评测脚本共用这一份逻辑。
"""

import os
import time
from collections.abc import AsyncIterator

from langgraph.errors import GraphRecursionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plugins.kbweb.agent import retrieval as R
from plugins.kbweb.agent.agent import CITE_RE, SYSTEM_PROMPT, build_agent, cited_ids
from plugins.kbweb.agent.llm import chat_model
from utils.log_util import logger

TOOL_LABEL = {
    'read_file': '读取规则', 'explore_concept': '概念关系汇总', 'query_evidence': '按条件查研究结果', 'search_literature': '检索文献题录', 'get_record': '阅读文献', 'search_passages': '语义检索全文段落',
    'search_recommendations': '检索指南推荐', 'search_findings': '检索研究发现', 'ls': '查看规则目录', 'glob': '查找规则', 'grep': '搜索规则',
    'write_todos': '规划步骤', 'task': '子任务',
}

_agents: dict[int, object] = {}


def graph_enabled() -> int:
    # 图检索工具的默认开关（按位：1=explore_concept，2=query_evidence，3=都开），评测时可逐次指定
    v = os.getenv('KBWEB_GRAPH_TOOL', '0').strip()
    return int(v) if v.isdigit() else 0


def agent_for(session_factory: async_sessionmaker[AsyncSession], graph: int = 0):
    # 同一个数据库引擎、同一种工具组合只建一次智能体（构建时要扫描技能目录）
    key = (id(session_factory.kw.get('bind')), graph)
    if key not in _agents:
        _agents[key] = build_agent(session_factory, graph)
    return _agents[key]


def _brief_args(name: str, args: dict) -> str:
    if name == 'read_file':
        return str(args.get('file_path', '')).replace('/skills/', '').replace('/SKILL.md', '')
    if name == 'query_evidence':
        return ' → '.join(str(args[k]) for k in ('intervention', 'outcome', 'population') if args.get(k))
    for k in ('keywords', 'query', 'term', 'source_id'):
        if args.get(k):
            v = args[k]
            return '、'.join(v) if isinstance(v, list) else str(v)
    return ''


FALLBACK_PROMPT = """检索已经结束，不能再调用工具。请只根据下面这些检索结果回答用户问题，遵守系统规则
（引用格式【source_id】，只引用下面出现过的编号；随访间隔只能来自规则表并标【待原文核对】；查不到就说明查不到）。

用户问题：{q}

检索结果：
{results}"""


async def _fallback_answer(question: str, tool_texts: list[str]) -> str:
    """智能体没能正常收尾（撞到步数上限）时，用已拿到的检索结果补一次不带工具的作答"""
    results = '\n\n---\n\n'.join(tool_texts)[-14000:]
    prompt = FALLBACK_PROMPT.replace('{q}', question).replace('{results}', results or '（无）')
    msg = await chat_model().ainvoke([{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': prompt}])
    return msg.content if isinstance(msg.content, str) else ''


async def run_events(session_factory: async_sessionmaker[AsyncSession], question: str, history: list[dict] | None = None,
                     recursion_limit: int = 80, graph: int | None = None) -> AsyncIterator[dict]:
    agent = agent_for(session_factory, graph_enabled() if graph is None else graph)
    messages = [{'role': m['role'], 'content': m['content']} for m in (history or [])[-6:] if m.get('role') in ('user', 'assistant')]
    messages.append({'role': 'user', 'content': question})
    seen: set[str] = set()  # 工具结果里出现过的 source_id，用来核对引用
    tool_texts: list[str] = []
    answer = ''
    t0 = time.time()
    try:
        async for ev in _stream(agent, messages, recursion_limit, seen, tool_texts, t0):
            if ev['type'] == 'answer_raw':
                answer = ev['content']
            else:
                yield ev
    except GraphRecursionError:
        logger.warning(f'问答达到步数上限，用已有检索结果补答：{question[:40]}')
        answer = ''
    if not answer:
        yield {'type': 'step', 'tool': 'fallback', 'label': '整理已有检索结果作答', 'detail': '', 'args': {}, 't': round(time.time() - t0, 1)}
        answer = await _fallback_answer(question, tool_texts)
    cites = cited_ids(answer)
    async with session_factory() as db:
        resolved = {r['source_id']: r for r in await R.resolve_sources(db, cites)}
    yield {'type': 'answer', 'content': answer, 't': round(time.time() - t0, 1)}
    yield {'type': 'citations', 'items': [
        {**resolved.get(s, {'source_id': s}), 'exists': s in resolved, 'retrieved': s in seen} for s in cites]}


async def _stream(agent, messages, recursion_limit, seen, tool_texts, t0) -> AsyncIterator[dict]:
    async for chunk in agent.astream({'messages': messages}, stream_mode='updates', config={'recursion_limit': recursion_limit}):
        for upd in chunk.values():
            for m in (upd or {}).get('messages', []) if isinstance(upd, dict) else []:
                if getattr(m, 'tool_calls', None):
                    for c in m.tool_calls:
                        yield {'type': 'step', 'tool': c['name'], 'label': TOOL_LABEL.get(c['name'], c['name']),
                               'detail': _brief_args(c['name'], c['args']), 'args': c['args'], 't': round(time.time() - t0, 1)}
                elif m.type == 'tool':
                    content = str(m.content)
                    ids = CITE_RE.findall(content)
                    seen.update(ids)
                    if ids:
                        tool_texts.append(content)
                    yield {'type': 'tool_result', 'tool': m.name, 'hits': len(set(ids)), 'error': content.startswith('Error'),
                           'content': content, 't': round(time.time() - t0, 1)}
                elif m.type == 'ai' and m.content:
                    yield {'type': 'answer_raw', 'content': m.content if isinstance(m.content, str) else ''.join(
                        p.get('text', '') for p in m.content if isinstance(p, dict))}
