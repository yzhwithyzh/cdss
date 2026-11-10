"""
循证问答智能体：deepagents + 只读检索工具 + 技能（SKILL.md，按题型给出回答规则）。

工具只读数据库；文件系统只开放 skills 目录且禁止写入。
"""

import json
import re
from pathlib import Path

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plugins.kbweb.agent import retrieval as R
from plugins.kbweb.agent.llm import chat_model

AGENT_DIR = Path(__file__).resolve().parent
CITE_RE = re.compile(r'【(\d+)】')

SYSTEM_PROMPT = """你是"结直肠腺瘤循证知识库"的问答助手，面向消化内镜医生。只依据工具检索到的库内内容回答。

## 库的范围
- 41,887 篇文献题录（PubMed/Embase/CNKI/万方/维普），含研究设计分类；其中 40 篇做了全文结构化（研究发现、效应值、推荐意见、原文定位）。
- 结构化入库的指南只有两部：中国早期结直肠癌指南（2024）、新加坡结肠息肉内镜处理（2022）。
- USMSTF 2020 / ESGE 2020 / BSG 2020 等随访指南**原文不在库内**。

## 工作方式
1. 先判断问题类型，用 read_file 读取对应技能的 SKILL.md 再动手（技能目录见下方 Skills 部分）：
   - 患者场景问随访间隔 → surveillance-interval（同时过一遍 applicability-check）
   - 风险/疗效/发生率等证据问题、涉及重复记录 → evidence-search
   - 特殊人群、取值冲突、术语版本等 → applicability-check
   - 指南比较、定义、版本 → guideline-compare
2. 检索时把中文问题转成英文医学术语和同义词（库内大多是英文文献），每次 6–10 个短语，不要中英文混在一次检索里。
   至少做两轮不同角度的 search_literature（如先查结局/风险，再查具体人群或干预），候选文献用 get_record 读摘要确认后再引用。
3. 引用格式：【source_id】，只能引用本轮工具结果里出现过的 source_id，绝不编造。
4. 用中文回答，先给结论，再给依据，最后写注意事项。答案控制在 600 字以内。
5. 库内 40 篇结构化文献的正文只能通过 search_passages 查到：**每个问题都至少调用一次 search_passages**（直接用问题原句即可）。
   题录检索结果里标"已结构化"的文献，用 get_record 看抽取出的研究发现，细节再用 search_passages(source_id=…) 查。
   只有 search_literature 和 search_passages 都查过之后，才能说"库内查不到"。
6. 查不到就说查不到；需要医生补充信息时直接说明缺什么。每题工具调用不超过 10 次，不要重复相同或近似的检索；
   search_passages 只覆盖 40 篇全文，查不到就换 search_literature，不要反复换词重试。
7. 随访间隔、分档阈值等数字，只能来自 surveillance-interval 技能里的表格（标【待原文核对】）或库内文献原文；
   不得凭经验写出"1–3 年""3 年左右"之类没有出处的数字，也不要把某篇文献没说过的内容挂在它的引用上。
"""


def _as_list(v: list[str] | str | None) -> list[str]:
    """模型有时把列表参数写成 JSON 字符串或逗号分隔串，这里统一成列表，避免工具调用直接报错"""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    v = v.strip()
    if v.startswith('['):
        try:
            return _as_list(json.loads(v))
        except json.JSONDecodeError:
            pass
    return [x.strip() for x in re.split(r'[,，;；]', v) if x.strip()]


def _fmt_lit(rows: list[dict], structured: set[int]) -> str:
    if not rows:
        return '未检索到相关文献。可换用其他英文同义词或更宽泛的检索词。'
    return '\n'.join(f"【{r['source_id']}】{r['year'] or '年份不详'} · {r['design'] or '设计未分类'}"
                     f"{' · 已结构化（可查全文）' if r['record_id'] in structured else ''} · {r['title']}" for r in rows)


PASSAGE_CHARS = 2000  # 每段返回给模型的最大字数（90% 的段落不超过 1260 字，基本整段返回）
PASSAGE_K = 10  # 证据可见性评测：6 条 → 10 条，答案原句出现在返回文字中的比例 81.5% → 90.7%
# 余弦距离上限：40 篇专项题答案段落的距离中位数 0.157，全库临床题最近段落中位数 0.283；
# 取 0.32 时专项题可见率 0.907 → 0.898，临床题每次塞给模型的无关原文从约 5500 字降到约 2400 字
PASSAGE_MAX_DISTANCE = 0.32


def _excerpt(text: str, terms: list[str], limit: int) -> str:
    """段落不超过 limit 时整段返回；更长时取与检索词重合最多的一截（只取开头时，段落后半的数字会被截掉）"""
    if len(text) <= limit:
        return text
    low = text.lower()
    words = {w for t in terms for w in R.terms_of(t) if len(w) >= 3}
    best, best_n = 0, -1
    for start in range(0, len(text) - limit + 200, 200):
        n = sum(low.count(w, start, start + limit) for w in words)
        if n > best_n:
            best, best_n = start, n
    return ('…' if best else '') + text[best:best + limit] + ('…' if best + limit < len(text) else '')


def fmt_passages(rows: list[dict], terms: list[str], limit: int = PASSAGE_CHARS) -> str:
    if not rows:
        return '未检索到相关段落。'
    return '\n\n'.join(f"【{r['source_id']}】第{r['page']}页 {r['title'][:60]}\n{_excerpt(r['text'], terms, limit)}" for r in rows)


GRAPH_PROMPT = """
8. 跨文献汇总类问题（"哪些文献/研究涉及 X""哪些因素与 X 有关""X 与哪些结局相关"）先调用 explore_concept(X)：
   它按概念把 40 篇结构化文献里所有相关的研究发现、推荐、断言、纳排条件一次性按文献列出；再逐篇引用，不要漏篇。
   概念名查不到时换同义词或中英文再试一次。
"""


EDGE_PROMPT = """
9. 问"X 对 Y 有什么作用/影响""哪些 X 能改善/降低 Y""X 在某人群中的效果""X 诊断 Y 的准确性"这类有明确干预/因素与结局的问题，
   先调用 query_evidence：intervention 填干预/暴露/检查，outcome 填结局，population 填人群（都用一个概念名，中英文均可），
   它会自动包含下位概念（如"图像增强内镜"含 NBI、LCI），只返回 40 篇结构化文献里符合条件的研究结果与效应值。
   默认只返回本研究自己的结果；没有结果时可用 include_cited=true 再查引用他人的结果（回答中要注明是转引）。
   概念找不到或没有结果时，换同义词/上位词再试一次，仍没有就用 search_passages 查原文。
"""

PREDICATE_LABEL = {'effect': '作用', 'diagnoses': '诊断', 'frequency': '发生率/水平', 'correlates': '相关',
                   'observed': '观察', 'recommended': '推荐'}
ROLE_LABEL = {'own_result': '本研究', 'cited_result': '转引', 'author_conclusion': '作者结论', 'recommendation': '指南推荐'}
DIRECTION_LABEL = {'increase': '↑显著', 'decrease': '↓显著', 'no_sig': '无显著差异', 'associated': '相关'}


def _fmt_edges(res: dict, per_doc: int = 10) -> str:
    names = {k: (v['name_zh'] or v['name_en']) for k, v in res['resolved'].items() if v}
    head = '检索条件：' + '；'.join(f'{k}={v}（含下位概念）' for k, v in names.items())
    if not res['rows']:
        return head + '\n40 篇结构化文献中没有符合条件的结果。可换同义词/上位词，或用 search_passages 查原文。'
    docs: dict[str, list[dict]] = {}
    for r in res['rows']:
        docs.setdefault(r['source_id'], []).append(r)
    lines = [head + f'\n共 {len(res["rows"])} 条，来自 {len(docs)} 篇文献：']
    for sid, rows in docs.items():
        lines.append(f"\n【{sid}】{rows[0]['pub_year'] or ''} · {rows[0]['title'][:80]}")
        for r in rows[:per_doc]:
            q = r['qualifiers']
            val = q.get('value_text') or q.get('action_text') or ''
            extra = '，'.join(x for x in (q.get('category'), q.get('arm') and f"组别 {q['arm']}", q.get('subgroup') and f"亚组 {q['subgroup']}",
                                          q.get('timepoint_text')) if x)
            vs = f" vs {r['comparator'] or q.get('comparator_text')}" if r['comparator'] or q.get('comparator_text') else ''
            lines.append(f"- [{ROLE_LABEL.get(r['statement_role'], r['statement_role'])}·{r['design']}] "
                         f"{r['subject'] or q.get('exposure_text') or ''}{vs} {PREDICATE_LABEL.get(r['predicate'], r['predicate'])} "
                         f"{r['object'] or q.get('outcome_text') or ''}：{q.get('measure') or ''} {val[:120]}"
                         f"{' ' + DIRECTION_LABEL[q['direction']] if q.get('direction') in DIRECTION_LABEL else ''}"
                         f"{'（' + extra + '）' if extra else ''}")
        if len(rows) > per_doc:
            lines.append(f'- ……另有 {len(rows) - per_doc} 条，可用 get_record 查看')
    return '\n'.join(lines)


REL_VERB = {'is_a': '属于', 'has_symptom': '表现为'}
# 关系表里 parent→child 的读法：is_a 是“子 属于 父”，has_symptom 是“疾病（父）表现为 症状（子）”
REL_SUBJECT_IS_PARENT = {'is_a': False, 'has_symptom': True}


def rel_phrase(rel_type: str, direction: str, me: str, other: str) -> str:
    """direction 沿用接口字段：'parent' 表示 other 是父端，'child' 表示 other 是子端"""
    parent, child = (other, me) if direction == 'parent' else (me, other)
    subj, obj = (parent, child) if REL_SUBJECT_IS_PARENT.get(rel_type, True) else (child, parent)
    return f'{subj} {REL_VERB.get(rel_type, rel_type)} {obj}'


def _rel_source(r: dict) -> str:
    if r.get('spans'):  # 有原文片段时给真实出处：文献、页码、原句
        sp = r['spans'][0]
        page = f"第{sp['page']}页" if sp.get('page') else ''
        return f"出处【{sp['source_id']}】{page}：“{(sp['quote'] or '')[:100]}…”"
    if r.get('source') == 'manual':
        return '人工/AI 补录，无库内原文'
    if r.get('source') == 'dx_vocab':
        return '受控词表（AI 审核 demo，非临床），无库内原文'
    return '来自文献，但原文片段未定位'


def _fmt_concept(ev: dict, others: list[dict]) -> str:
    c = ev['concept']
    lines = [f"概念：{c['name_zh'] or ''} {c['name_en'] or ''}（{c['concept_type']}）；在 {len(ev['docs'])} 篇结构化文献中有证据条目，"
             f"{len(ev['relations'])} 条已审核的概念关系"]
    if c.get('aliases'):
        lines.append('别名：' + '、'.join(a['alias'] for a in c['aliases'][:12]))
    if ev['relations']:
        me = c['name_zh'] or c['name_en']
        lines.append('概念关系（可再用 explore_concept 查相关概念的证据）：')
        for r in ev['relations'][:15]:
            lines.append(f"- {rel_phrase(r['rel_type'], r['dir'], me, r['name'])}（{_rel_source(r)}）")
    if others:
        lines.append('其他可能匹配的概念：' + '、'.join(o['name_zh'] or o['name_en'] for o in others))
    for d in ev['docs']:
        lines.append(f"\n【{d['source_id']}】{d['title'][:80]}")
        for kind, x in d['items'][:12]:
            if kind == '发现':
                res = '；'.join(f"{r['measure']}{'(' + r['category'] + ')' if r['category'] else ''} {r['value_as_reported']}" for r in x['results'][:3])
                vs = f" vs {x['comparator_as_reported']}" if x['comparator_as_reported'] else ''
                lines.append(f"- 发现：{x['exposure_as_reported'] or x['exposure_concept'] or ''}{vs} → {x['outcome_label'] or x['outcome_concept'] or ''}：{res}")
            elif kind == '推荐':
                lines.append(f"- 推荐 {x['number_as_reported']}：{x['text_verbatim'][:260]}（{x['strength_as_reported'] or ''}）")
            elif kind == '断言':
                ctx = f"（{x['context']}）" if x['context'] else ''
                lines.append(f"- 断言：{x['subject']} {x['predicate']} {x['object'] or ''}{ctx}")
            else:
                lines.append(f"- 人群条件（{x['kind']}）：{x['value_as_reported'] or ''}；人群：{x['population_label'] or ''}")
        if len(d['items']) > 12:
            lines.append(f"- ……另有 {len(d['items']) - 12} 条，可用 get_record 查看")
    return '\n'.join(lines)


GRAPH_CONCEPT, GRAPH_EDGE = 1, 2  # 图检索工具开关（按位）：1=explore_concept，2=query_evidence


def build_tools(session_factory: async_sessionmaker[AsyncSession], graph: int = 0) -> list:
    """工具各自开会话：模型一次发出多个工具调用时会并发执行，不能共用一个 AsyncSession。
    graph 按位打开图检索工具：1=按概念汇总（explore_concept），2=按 PICO 约束查关系表（query_evidence）"""

    @tool
    async def search_literature(keywords: list[str] | str, design: str | None = None, year_from: int | None = None, limit: int = 20) -> str:
        """在全部 4.2 万篇文献题录（标题+摘要）中检索。
        keywords: 6–10 个英文检索短语（术语 + 同义词/缩写，如 ["piecemeal EMR", "EPMR", "local recurrence", "residual adenoma"]）。
            不要中英文混用：要查中文文献时，单独用中文检索词再调用一次。
        design: 可选，按研究设计过滤，取值如 "系统评价"、"随机对照"、"队列"、"病例对照"。
        year_from: 可选，只要该年及以后发表的文献。
        返回：每行【source_id】年份 · 研究设计 · 标题。"""
        async with session_factory() as db:
            # 不自动追加概念别名：评测显示混入中文词会把中文文献顶到前面，必中文献召回减半
            kws = _as_list(keywords)
            rows = await R.search_literature(db, kws, design=design, year_from=year_from, limit=min(limit, 30))
            return _fmt_lit(rows, await R.structured_record_ids(db))

    @tool
    async def get_record(source_id: str) -> str:
        """打开一篇文献：题录、研究设计、摘要；若已结构化，附研究发现（效应值）和推荐意见。引用前应先读摘要确认内容。"""
        async with session_factory() as db:
            d = await R.record_brief(db, source_id)
        if not d:
            return f'找不到 source_id={source_id}'
        r, kb = d['record'], d['kb']
        lines = [f"【{r['source_id']}】{r['title']}", f"{r['pub_year']} · {r['design_label'] or '设计未分类'} · {r['source_db'] or ''}",
                 f"摘要：{(r['abstract'] or '无摘要')[:1800]}"]
        if kb:
            lines.append(f"（已结构化：共 {len(kb['findings'])} 条研究发现、{len(kb['recommendations'])} 条推荐；正文细节用 search_passages(source_id=\"{r['source_id']}\") 查）")
            for f in kb['findings'][:60]:
                res = '；'.join(f"{x['measure']}{'(' + x['category'] + ')' if x['category'] else ''} {x['value_as_reported']}" for x in f['results'][:4])
                lines.append(f"- 发现：{f['exposure_as_reported'] or f['exposure_concept'] or ''}"
                             f"{' vs ' + f['comparator_as_reported'] if f['comparator_as_reported'] else ''} → {f['outcome_label'] or ''}：{res}")
            for rc in kb['recommendations'][:30]:
                lines.append(f"- 推荐 {rc['number_as_reported']}：{rc['text_verbatim'][:300]}")
        return '\n'.join(lines)

    @tool
    async def search_passages(query: str, keywords: list[str] | str | None = None, source_id: str | None = None) -> str:
        """在 40 篇已解析全文的正文段落里做语义+关键词检索，找具体数字、方法细节、原文表述。
        query: 自然语言问题（中英文均可）；keywords: 可选英文检索词；source_id: 可选，限定在某篇文献内。"""
        async with session_factory() as db:
            rid = None
            if source_id:
                rid = (await R.resolve_sources(db, [source_id]) or [{}])[0].get('record_id')
            kws = _as_list(keywords)
            rows = await R.search_passages(db, query, kws, k=PASSAGE_K, record_id=rid, max_distance=PASSAGE_MAX_DISTANCE)
        if not rows:
            return '40 篇结构化全文中没有与该问题足够相关的段落（这些文献可能不涉及此问题），请以 search_literature 题录检索为主。'
        return fmt_passages(rows, kws + [query])

    @tool
    async def search_recommendations(keywords: list[str] | str) -> str:
        """检索库内两部结构化指南（中国 2024、新加坡 2022）的推荐意见，返回条款原文、强度。keywords 用英文术语。"""
        async with session_factory() as db:
            rows = await R.search_structured(db, _as_list(keywords), 'recommendation')
            srcs = await R.source_ids_by_record(db, [r['record_id'] for r in rows])
        if not rows:
            return '库内两部指南中没有相关推荐。'
        return '\n'.join(f"【{srcs.get(r['record_id'], '?')}】{r['guideline_title'][:50]} 推荐 {r['number_as_reported']}："
                         f"{r['text_verbatim'][:400]}（{r['strength_as_reported'] or ''}；{r['certainty_as_reported'] or ''}）" for r in rows)

    @tool
    async def search_findings(keywords: list[str] | str) -> str:
        """检索 40 篇结构化文献里抽取的研究发现与效应值（OR/HR/RR/比例等）。
        keywords 中英文都给（如 ["proximal colon", "近端结肠", "adenoma", "腺瘤"]）：中文文献的抽取字段是中文。"""
        async with session_factory() as db:
            rows = await R.search_structured(db, _as_list(keywords), 'finding')
            srcs = await R.source_ids_by_record(db, [r['record_id'] for r in rows])
        if not rows:
            return '结构化研究发现中没有相关条目。'
        out = []
        for f in rows:
            res = '；'.join(f"{x['measure']}{'(' + x['category'] + ')' if x['category'] else ''} {x['value_as_reported']}" for x in f['results'][:4])
            out.append(f"【{srcs.get(f['record_id'], '?')}】{f['exposure_as_reported'] or f['exposure_concept'] or ''}"
                       f"{' vs ' + f['comparator_as_reported'] if f['comparator_as_reported'] else ''} → {f['outcome_label'] or ''}：{res}")
        return '\n'.join(out)

    @tool
    async def explore_concept(term: str) -> str:
        """按概念（类知识图谱）汇总：输入一个医学概念的中文或英文名（如 "阿司匹林"、"cecal intubation rate"），
        返回该概念在 40 篇结构化文献中的全部关联——以它为暴露或结局的研究发现（含效应值）、涉及它的推荐、断言、
        人群纳排条件，以及上下位概念，按文献分组。适合"哪些文献/研究涉及 X""X 与哪些因素/结局有关"这类汇总问题。"""
        async with session_factory() as db:
            found = await R.find_concepts(db, term)
            if not found:
                return f'概念库中没有找到"{term}"，可换同义词、中英文或更上位的术语再试。'
            ev = await R.concept_evidence(db, found[0]['concept_id'])
        if not ev or (not ev['docs'] and not ev['relations']):
            return f'概念"{term}"存在，但 40 篇结构化文献中没有与它关联的条目，也没有已审核的概念关系。'
        return _fmt_concept(ev, found[1:])

    @tool
    async def query_evidence(intervention: str | None = None, outcome: str | None = None, population: str | None = None,
                             design: list[str] | str | None = None, include_cited: bool = False) -> str:
        """按"干预/因素 → 结局 + 人群 + 研究设计"约束，查 40 篇结构化文献抽取出的研究结果（效应值、比例、诊断准确性）和指南推荐。
        intervention: 干预、暴露因素或检查（如 "阿司匹林"、"图像增强内镜"、"cold snare polypectomy"）；
        outcome: 结局（如 "腺瘤检出率"、"腺瘤复发"）；population: 人群或病变（如 "进展期腺瘤"）。三者至少填一个，各填一个概念名。
        每个概念自动包含其下位概念。design: 可选研究设计 rct / sr_ma / cohort / case_control / cross_sectional / guideline。
        include_cited: 默认 false 只要本研究结果；true 时包括文献中转引他人的结果。"""
        if not (intervention or outcome or population):
            return '请至少填写 intervention、outcome、population 中的一个。'
        async with session_factory() as db:
            res = await R.query_edges(db, subject=intervention, outcome=outcome, population=population,
                                      own_only=not include_cited, designs=_as_list(design) or None)
        missing = [t for slot, t in (('subject', intervention), ('outcome', outcome), ('population', population))
                   if t and not res['resolved'][slot]]
        if missing:
            return f'概念库中没有找到：{"、".join(missing)}。可换同义词、中英文或上位术语再试。'
        return _fmt_edges(res)

    tools = [search_literature, get_record, search_passages, search_recommendations, search_findings]
    return tools + ([explore_concept] if graph & GRAPH_CONCEPT else []) + ([query_evidence] if graph & GRAPH_EDGE else [])


def build_agent(session_factory: async_sessionmaker[AsyncSession], graph: int = 0):
    return create_deep_agent(
        model=chat_model(),
        tools=build_tools(session_factory, graph),
        system_prompt=SYSTEM_PROMPT + (GRAPH_PROMPT if graph & GRAPH_CONCEPT else '') + (EDGE_PROMPT if graph & GRAPH_EDGE else ''),
        skills=['/skills/'],
        backend=FilesystemBackend(root_dir=AGENT_DIR, virtual_mode=True),
        permissions=[FilesystemPermission(operations=['write'], paths=['/**'], mode='deny')],
        # 评测中出现过同一段落检索连调 13 次撞到递归上限；超额调用被拦下后模型会用已有结果作答
        middleware=[
            ToolCallLimitMiddleware(run_limit=10, exit_behavior='continue'),
            ToolCallLimitMiddleware(tool_name='search_passages', run_limit=4, exit_behavior='continue'),
        ],
        name='kb-qa',
    )


def cited_ids(answer: str) -> list[str]:
    return list(dict.fromkeys(CITE_RE.findall(answer or '')))
