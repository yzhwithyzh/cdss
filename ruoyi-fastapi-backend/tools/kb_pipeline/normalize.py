"""
症状归一（设计方案 4.4）：受控词表 → 规则预处理 → 候选召回 → 模型判定关系 → 是否需人工审核。

两种入口：
- normalize_term(写法)：建库用，一个原文写法 → 一个或多个标准症状
- normalize_text(描述)：门诊用，一段患者描述 → 症状提及（含否定/既往/可能、属性）→ 各自归一

关系类型：SAME 同义 / NARROWER 更具体（修饰语转属性）/ BROADER 更宽泛 / COMPOUND 复合（拆开）/ NOT_SYMPTOM 不是症状 / NONE 词表外。
只有“归一化写法与同义词完全一致”的结果可直接采用；其余都标 needs_review，由人审核（建库）或在回答中提示（门诊）。
"""

import asyncio
import json
import re
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools.kb_pipeline.terms import keys, norm

RELATIONS = ('SAME', 'NARROWER', 'BROADER', 'COMPOUND', 'NOT_SYMPTOM', 'NONE')
ATTRS = ('部位', '性质', '时程', '程度', '颜色', '与粪便关系', '诱因', '情境')
# 规则只按标点拆；“或/和/伴/and”等连词是否构成复合交给模型判断（“便秘和腹泻交替”不能拆）
SPLIT_RE = re.compile(r'[、，,；;/]')


@dataclass
class Entry:
    concept_id: int
    code: str
    name: str
    name_en: str
    aliases: list[str]
    parents: list[str] = field(default_factory=list)  # 上位概念编码
    keys: set[str] = field(default_factory=set)


@dataclass
class Mapping:
    text: str
    relation: str
    code: str | None = None
    name: str | None = None
    attributes: dict = field(default_factory=dict)
    method: str = 'model'  # exact / model
    needs_review: bool = True
    reason: str = ''
    status: str = 'present'  # 门诊描述：present / absent / history / possible
    candidates: list = field(default_factory=list)


def split_term(term: str) -> list[str]:
    """按标点拆开并列写法：“腹胀、腹痛、便秘”“tenesmus, straining, incontinence”"""
    parts = [p.strip(' 　.。') for p in SPLIT_RE.split(term or '')]
    return [p for p in parts if p]


def bigrams(s: str) -> set[str]:
    s = norm(s).replace(' ', '')
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


JUDGE = """你是消化科术语标准化专家。判断“原文写法”与候选标准症状的关系，只能从候选中选编码。
关系定义：
- SAME：同义（口语、英文、缩写都算），选对应编码
- NARROWER：原文是某个候选加上修饰（部位/性质/时程/程度/颜色/与粪便关系/诱因/情境），选该候选编码，并把修饰写进 attributes
- BROADER：原文比所有候选都宽泛，不能确定是哪一个（如“出血”），code 为 null
- COMPOUND：原文包含多个不同症状，在 parts 中列出拆开后的写法，code 为 null
- NOT_SYMPTOM：原文是疾病、检查、病史、人口学特征等，不是症状/体征，code 为 null
- NONE：是症状但不在候选里（如咳嗽、尿血），code 为 null
注意：便血（鲜红/暗红，下消化道）与黑便（柏油样，上消化道）不同；贫血与缺铁性贫血不同；大便潜血阳性不是便血。不确定时不要硬选。
只输出 JSON：{"relation": "...", "code": "SYM:..." 或 null, "attributes": {...}, "parts": [...], "reason": "一句话"}

原文写法：{term}
候选：
{cands}"""

EXTRACT = """从门诊患者描述中提取症状和体征。化验和查体发现的异常（如缺铁性贫血、大便潜血阳性、腹部包块、直肠指检触及肿物）也要提取；
不要提取疾病诊断、检查项目名称本身、既往疾病史、年龄性别。描述排便规律变化的整体说法（如“便秘腹泻交替”）作为一个提及，不要拆开。
每个提及给出：mention（原文中的症状说法，不含修饰语）、status（present 有 / absent 明确否认 / history 既往有现已无 / possible 不确定）、
attributes（部位/性质/时程/程度/颜色/与粪便关系/诱因/情境，原文没说的不写）。
只输出 JSON：{"mentions": [{"mention": "...", "status": "...", "attributes": {...}}]}

描述：{text}"""


def _json(s: str) -> dict:
    m = re.search(r'\{.*\}', s or '', re.S)
    return json.loads(m.group(0)) if m else {}


class SymptomNormalizer:
    def __init__(self, entries: list[Entry], vectors: dict[str, np.ndarray] | None = None, disease_keys: dict[str, str] | None = None):
        self.entries = entries
        self.disease_keys = disease_keys or {}  # 疾病名称/同义词的归一化键 → 疾病名：命中即“不是症状”，不交给模型
        self.by_code = {e.code: e for e in entries}
        self.vectors = vectors or {}  # 写法 → 归一化向量（召回用，可缺省为纯字面）

    # ---------------- 词表 ----------------
    @classmethod
    async def load(cls, db: AsyncSession, with_vectors: bool = True) -> 'SymptomNormalizer':
        rows = (await db.execute(text("""
            SELECT c.concept_id, c.code, coalesce(c.name_zh, c.name_en), coalesce(c.name_en, ''),
                   coalesce((SELECT array_agg(a.alias) FROM kb_concept_alias a WHERE a.concept_id = c.concept_id), '{}'),
                   coalesce((SELECT array_agg(p.code) FROM kb_concept_relation r JOIN kb_concept p ON p.concept_id = r.parent_id
                             WHERE r.child_id = c.concept_id AND r.rel_type = 'is_a' AND r.review_status IS DISTINCT FROM 'rejected'
                               AND p.code LIKE 'SYM:%'), '{}')
            FROM kb_concept c WHERE c.status = 'active' AND c.concept_type = 'symptom_or_sign' AND c.code LIKE 'SYM:%'
            ORDER BY c.code"""))).all()
        entries = []
        for cid, code, name, name_en, aliases, parents in rows:
            names = list(dict.fromkeys([name, name_en, *aliases]))
            e = Entry(cid, code, name, name_en, [n for n in names if n], list(parents))
            e.keys = set().union(*(keys(n) for n in e.aliases))
            entries.append(e)
        diseases = (await db.execute(text("""
            SELECT coalesce(c.name_zh, c.name_en), a.alias FROM kb_concept c JOIN kb_concept_alias a ON a.concept_id = c.concept_id
            WHERE c.status = 'active' AND c.concept_type IN ('disease_or_lesion', 'genetic_syndrome')"""))).all()
        symptom_keys = set().union(*(e.keys for e in entries))
        disease_keys = {k: name for name, alias in diseases for k in keys(alias) if k not in symptom_keys}
        vectors = await cls._embed([a for e in entries for a in e.aliases]) if with_vectors else {}
        return cls(entries, vectors, disease_keys)

    @staticmethod
    async def _embed(texts: list[str]) -> dict[str, np.ndarray]:
        from tools.kb_pipeline.embed import embed_batch, load_env  # noqa: PLC0415

        env = load_env('dev')
        if not env.get('DASHSCOPE_API_KEY'):
            return {}
        out: dict[str, np.ndarray] = {}
        uniq = list(dict.fromkeys(texts))
        for i in range(0, len(uniq), 10):
            batch = uniq[i:i + 10]
            vecs, _ = await asyncio.to_thread(embed_batch, env, 'qwen3.7-text-embedding', 1024, batch)
            for t, v in zip(batch, vecs, strict=True):
                a = np.asarray(v, dtype=np.float32)
                out[t] = a / (np.linalg.norm(a) or 1)
        return out

    # ---------------- 第④步：候选召回 ----------------
    def exact(self, term: str) -> Entry | None:
        k = keys(term)
        hits = [e for e in self.entries if e.keys & k]
        return hits[0] if len(hits) == 1 else None

    async def recall(self, term: str, k: int = 5) -> list[tuple[Entry, float]]:
        qv = (await self._embed([term])).get(term) if self.vectors else None
        tb = bigrams(term)
        scored = []
        for e in self.entries:
            best = 0.0
            for a in e.aliases:
                jac = len(tb & bigrams(a)) / len(tb | bigrams(a))
                cos = float(qv @ self.vectors[a]) if qv is not None and a in self.vectors else 0.0
                best = max(best, 0.6 * cos + 0.4 * jac if qv is not None else jac)
            scored.append((e, best))
        top = sorted(scored, key=lambda x: -x[1])[:k]
        # 带上候选的上位概念（如 便血 → 消化道出血），让模型能选到更宽泛但仍在词表里的概念
        have = {e.code for e, _ in top}
        extra = [p for e, _ in top for p in e.parents if p in self.by_code and p not in have]
        return top + [(self.by_code[p], 0.0) for p in dict.fromkeys(extra)]

    # ---------------- 第⑤步：模型判定 ----------------
    async def judge(self, term: str, cands: list[tuple[Entry, float]]) -> dict:
        from plugins.kbweb.agent.llm import chat_model  # noqa: PLC0415

        lines = '\n'.join(f"- {e.code}｜{e.name}｜同义词：{'、'.join(a for a in e.aliases if a != e.name)[:120]}"
                          f"{'｜上位：' + '、'.join(e.parents) if e.parents else ''}" for e, _ in cands)
        prompt = JUDGE.replace('{term}', term).replace('{cands}', lines)
        for attempt in range(3):
            try:
                msg = await chat_model(0.0).ainvoke(prompt)
                return _json(str(msg.content))
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                await asyncio.sleep(3 * (attempt + 1))
        return {}

    async def normalize_term(self, term: str, _depth: int = 0) -> list[Mapping]:
        term = (term or '').strip()
        if not term:
            return []
        e = self.exact(term)
        if e:
            return [Mapping(term, 'SAME', e.code, e.name, method='exact', needs_review=False, reason='与同义词完全一致')]
        hit = next((self.disease_keys[k] for k in keys(term) if k in self.disease_keys), None)
        if hit:  # 疾病名不能被当成症状同义词（如“直肠脱垂”≠“肛门肿物脱出”）
            return [Mapping(term, 'NOT_SYMPTOM', method='exact', needs_review=False, reason=f'是疾病“{hit}”，不是症状')]
        parts = split_term(term)
        if len(parts) > 1 and _depth == 0:
            out = []
            for p in parts:
                out += await self.normalize_term(p, 1)
            return out
        cands = await self.recall(term)
        d = await self.judge(term, cands)
        rel = d.get('relation') if d.get('relation') in RELATIONS else 'NONE'
        # 标点拆开后的片段仍可能是复合（“straining and incontinence”），允许模型再拆一层
        if rel == 'COMPOUND' and _depth <= 1 and d.get('parts'):
            out = []
            for p in d['parts']:
                out += await self.normalize_term(str(p), 2)
            return out
        code = d.get('code') if d.get('code') in {c.code for c, _ in cands} else None
        if rel in ('SAME', 'NARROWER') and not code:
            rel = 'NONE'
        attrs = {k: v for k, v in (d.get('attributes') or {}).items() if v}
        return [Mapping(term, rel, code, self.by_code[code].name if code else None, attrs, 'model', True,
                        d.get('reason', ''), candidates=[(c.code, round(s, 3)) for c, s in cands])]

    # ---------------- 门诊描述 ----------------
    async def extract(self, description: str) -> list[dict]:
        from plugins.kbweb.agent.llm import chat_model  # noqa: PLC0415

        msg = await chat_model(0.0).ainvoke(EXTRACT.replace('{text}', description))
        return [m for m in _json(str(msg.content)).get('mentions', []) if m.get('mention')]

    async def normalize_text(self, description: str) -> list[Mapping]:
        out = []
        for m in await self.extract(description):
            for r in await self.normalize_term(m['mention']):
                r.status = m.get('status') or 'present'
                r.attributes = {**{k: v for k, v in (m.get('attributes') or {}).items() if v}, **r.attributes}
                out.append(r)
        return out
