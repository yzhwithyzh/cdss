"""
问答检索用的底层检索函数（不依赖大模型，可单独评测）。

- 题录检索：41k 题名+摘要在内存里建倒排索引做 BM25（数据库 ILIKE 每个词要全表扫 0.6s，太慢；不改库结构）
- 段落检索：向量（pgvector，kb_block_embedding）+ 关键词，两路用 RRF 融合
- 术语扩展：问句里出现的概念别名 → 该概念的全部中英文别名
所有结果都带 source_id，回答里用【source_id】引用。
"""

import asyncio
import math
import re
from dataclasses import dataclass

import jieba
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.kbweb.dao.kbweb_dao import KbwebDao
from utils.log_util import logger

jieba.setLogLevel(60)

# 问句里常见但对检索没有区分度的词
STOPWORDS = set(
    '的 了 吗 呢 么 是 有 和 与 及 或 在 对 把 被 会 要 能 可以 应该 什么 怎么 如何 为什么 多少 哪些 哪个 是否 还是 以后 之后 后 时 '
    '下次 一个 这个 那个 这些 患者 病人 男性 女性 岁 枚 个 做 再 吗？ 呢？ 情况 问题 需要 建议 推荐 多久 时间 风险 研究 证据 文献 '
    'the of and or in on for with to a an is are be by vs from at as after before patients patient study'.split()
)
TOKEN_RE = re.compile(r'[A-Za-z][A-Za-z0-9\-]+|\d+(?:\.\d+)?\s*mm|[一-鿿]+')


def tokenize(q: str) -> list[str]:
    """中文用 jieba 切词，英文按词；去停用词和单字"""
    out: list[str] = []
    for m in TOKEN_RE.findall(q or ''):
        if re.match(r'[一-鿿]', m):
            out += [w for w in jieba.cut(m) if len(w) >= 2 and w not in STOPWORDS]
        elif m.lower() not in STOPWORDS and len(m) >= 3:
            out.append(m.lower())
    return list(dict.fromkeys(out))


async def expand_terms(db: AsyncSession, q: str, limit: int = 24) -> list[str]:
    """问句中命中的概念 → 其全部别名（中英文），用于跨语言检索"""
    rows = (await db.execute(text("""
        WITH hit AS (
            SELECT DISTINCT a.concept_id FROM kb_concept_alias a
            WHERE length(a.alias) >= CASE WHEN a.alias ~ '[一-鿿]' THEN 2 ELSE 4 END
              AND position(lower(a.alias) IN lower(:q)) > 0
        )
        SELECT DISTINCT a.alias FROM kb_concept_alias a JOIN hit ON hit.concept_id = a.concept_id
        UNION SELECT k.name_en FROM kb_concept k JOIN hit ON hit.concept_id = k.concept_id WHERE k.name_en IS NOT NULL
    """), {'q': q})).scalars().all()
    terms = [t.strip() for t in rows if t and 3 <= len(t.strip()) <= 60 and t.strip().lower() not in q.lower()]
    return sorted(set(terms), key=len)[:limit]


WORD_RE = re.compile(r'[a-z0-9]+|[一-鿿]+')
EN_STOP = set('the of and or in on for with to a an is are was were be by vs from at as after before than that this these those its their '
              'patients patient study studies using use used based between among during within into over under who which'.split())


def _stem(w: str) -> str:
    # 极简归一：复数、-ies；足够让 adenomas/adenoma、polyps/polyp 对上
    if len(w) > 4 and w.endswith('ies'):
        return w[:-3] + 'y'
    if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w


def terms_of(s: str) -> list[str]:
    """英文按词（去停用词、归一复数，连字符视为分隔），中文按二元字组"""
    out: list[str] = []
    for m in WORD_RE.findall((s or '').lower()):
        if '一' <= m[0] <= '鿿':
            out += [m[i:i + 2] for i in range(len(m) - 1)] or [m]
        elif m not in EN_STOP and (len(m) >= 2 or m.isdigit()):
            out.append(_stem(m))
    return out


@dataclass
class _Corpus:
    ids: list[int]
    source_ids: list[str]
    titles: list[str]
    years: list[int | None]
    designs: list[str | None]
    title_lc: list[str]
    full_lc: list[str]
    postings: dict  # 词 → (文献下标数组, 词频数组)；标题里的词频按 3 倍计
    doc_len: object  # numpy 数组
    avg_len: float


_corpus: _Corpus | None = None
_corpus_lock = asyncio.Lock()


def _build_index(full: list[str], titles_lc: list[str]):
    import numpy as np  # noqa: PLC0415

    acc: dict[str, dict[int, int]] = {}
    lens = np.zeros(len(full), dtype=np.float32)
    for i, (doc, title) in enumerate(zip(full, titles_lc, strict=True)):
        tw = terms_of(title)
        bw = terms_of(doc[len(title):])
        lens[i] = len(tw) * 3 + len(bw)
        for t in tw:
            d = acc.setdefault(t, {})
            d[i] = d.get(i, 0) + 3
        for t in bw:
            d = acc.setdefault(t, {})
            d[i] = d.get(i, 0) + 1
    postings = {t: (np.fromiter(d.keys(), dtype=np.int32, count=len(d)), np.fromiter(d.values(), dtype=np.float32, count=len(d)))
                for t, d in acc.items()}
    return postings, lens, float(lens.mean() or 1)


async def corpus(db: AsyncSession) -> _Corpus:
    """首次调用时加载全部题录并建倒排索引，之后常驻内存"""
    global _corpus  # noqa: PLW0603
    if _corpus is None:
        async with _corpus_lock:
            if _corpus is None:
                rows = (await db.execute(text("""
                    SELECT r.record_id, r.source_id, r.title, r.pub_year, sd.design_label, coalesce(r.abstract, '')
                    FROM lit_record r LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
                    WHERE r.del_flag = '0' ORDER BY r.record_id"""))).all()
                title_lc = [(r[2] or '').lower() for r in rows]
                full_lc = [t + ' \n ' + r[5].lower() for t, r in zip(title_lc, rows, strict=True)]
                postings, lens, avg = await asyncio.to_thread(_build_index, full_lc, title_lc)
                _corpus = _Corpus(
                    ids=[r[0] for r in rows], source_ids=[r[1] for r in rows], titles=[r[2] or '' for r in rows],
                    years=[r[3] for r in rows], designs=[r[4] for r in rows], title_lc=title_lc, full_lc=full_lc,
                    postings=postings, doc_len=lens, avg_len=avg,
                )
    return _corpus


def _score(c: _Corpus, keywords: list[str]) -> dict[int, float]:
    """BM25（标题词频×3）+ 整个短语原样出现的加分；同一个词在多个检索短语里只算一次"""
    import numpy as np  # noqa: PLC0415

    n = len(c.ids)
    k1, b = 1.2, 0.75
    total = np.zeros(n, dtype=np.float32)
    for t in dict.fromkeys(w for k in keywords for w in terms_of(k)):
        if t not in c.postings:
            continue
        docs, tf = c.postings[t]
        idf = math.log(1 + (n - len(docs) + 0.5) / (len(docs) + 0.5))
        norm = k1 * (1 - b + b * c.doc_len[docs] / c.avg_len)
        total[docs] += idf * tf * (k1 + 1) / (tf + norm)
    # 多词短语整体命中：说明概念完整出现，在得分前 2000 篇里加分
    phrases = {re.sub(r'[-_/]+', ' ', k.lower().strip()) for k in keywords if len(terms_of(k)) >= 2}
    if phrases:
        for i in np.argsort(-total)[:2000]:
            if total[i] <= 0:
                break
            s = c.full_lc[i].replace('-', ' ')
            t = c.title_lc[i].replace('-', ' ')
            for k in phrases:
                if k in s:
                    total[i] += 1.5 if k in t else 0.8
    nz = np.nonzero(total)[0]
    return {int(i): float(total[i]) for i in nz}


async def search_literature(
    db: AsyncSession, keywords: list[str], *, design: str | None = None, year_from: int | None = None, limit: int = 10,
) -> list[dict]:
    c = await corpus(db)
    terms = [k for k in keywords if k and len(k.strip()) >= 2]
    scores = await asyncio.to_thread(_score, c, terms)
    order = sorted(scores, key=lambda i: (-scores[i], -(c.years[i] or 0)))
    out = []
    for i in order:
        if design and (c.designs[i] or '').find(design) < 0:
            continue
        if year_from and (c.years[i] or 0) < year_from:
            continue
        out.append({'record_id': c.ids[i], 'source_id': c.source_ids[i], 'title': c.titles[i], 'year': c.years[i],
                    'design': c.designs[i], 'score': round(scores[i], 2)})
        if len(out) >= limit:
            break
    return out


# ======================= 段落（向量 + 关键词） =======================
_embed_cache: dict[str, list[float]] = {}


async def embed_query(q: str) -> list[float] | None:
    """用建索引时同一个模型生成查询向量；没配密钥或调用失败时返回 None（退化为纯关键词），失败会记日志"""
    from tools.kb_pipeline.embed import embed_batch, load_env  # noqa: PLC0415

    if q in _embed_cache:
        return _embed_cache[q]
    env = load_env('dev')
    if not env.get('DASHSCOPE_API_KEY') or not env.get('LIT_EMBED_BASE_URL'):
        return None
    try:
        vecs, _ = await asyncio.to_thread(embed_batch, env, 'qwen3.7-text-embedding', 1024, [q])
    except Exception as e:  # noqa: BLE001
        logger.warning(f'查询向量生成失败，段落检索退化为关键词：{e}')
        return None
    if len(_embed_cache) > 2000:
        _embed_cache.clear()
    _embed_cache[q] = vecs[0]
    return vecs[0]


async def search_passages(
    db: AsyncSession, query: str, keywords: list[str] | None = None, *, k: int = 8, record_id: int | None = None,
    use_vector: bool = True, use_keyword: bool | None = None, max_distance: float | None = None,
) -> list[dict]:
    """默认只用向量：40 题段落评测中纯向量 R@10=0.975，融合关键词反而降到 0.75。
    向量不可用（未配密钥/调用失败）时自动退化为关键词；use_keyword=True 可强制融合。
    max_distance：余弦距离上限，超过的段落不返回（问题与 40 篇全文无关时，避免把大段无关原文塞给模型）。"""
    rank: dict[int, float] = {}
    dist: dict[int, float] = {}
    vec = await embed_query(query) if use_vector else None
    if use_keyword is None:
        use_keyword = vec is None
    rid_sql = 'AND d.record_id = :rid' if record_id else ''
    p = {'rid': record_id} if record_id else {}
    if vec is not None:
        rows = (await db.execute(text(f"""
            SELECT e.block_id, e.embedding <=> CAST(:v AS vector) AS dist FROM kb_block_embedding e JOIN kb_block b ON b.block_id = e.block_id
            JOIN kb_document_version v ON v.version_id = b.version_id JOIN kb_document d ON d.document_id = v.document_id
            WHERE e.build_id = (SELECT max(build_id) FROM kb_index_build WHERE model_id = 'qwen3.7-text-embedding') {rid_sql}
            ORDER BY e.embedding <=> CAST(:v AS vector) LIMIT 40"""), {**p, 'v': str(vec)})).all()
        for r, (bid, d) in enumerate(rows):
            dist[bid] = float(d)
            if max_distance is None or d <= max_distance:
                rank[bid] = rank.get(bid, 0) + 1 / (60 + r)
    # 关键词路只用调用方给的检索词（通常是模型改写出的英文术语）：中文问句分词碰不到英文原文
    terms = [t for t in dict.fromkeys(keywords or []) if len(t) >= 3][:12] if use_keyword else []
    if terms:
        cond = ' + '.join(f'(b.text ILIKE :t{i})::int' for i in range(len(terms)))
        rows = (await db.execute(text(f"""
            SELECT block_id FROM (
                SELECT b.block_id, ({cond}) AS n FROM kb_block b JOIN kb_document_version v ON v.version_id = b.version_id
                JOIN kb_document d ON d.document_id = v.document_id
                WHERE b.block_type IN ('paragraph', 'list_item', 'table', 'caption') {rid_sql}) x
            WHERE n > 0 ORDER BY n DESC, block_id LIMIT 40"""), {**p, **{f't{i}': f'%{t}%' for i, t in enumerate(terms)}})).scalars().all()
        # 关键词路只作补充：权重减半，避免把向量路的好结果挤下去
        for r, bid in enumerate(rows):
            rank[bid] = rank.get(bid, 0) + 0.5 / (60 + r)
    top = sorted(rank, key=lambda b: -rank[b])[:k]
    if not top:
        return []
    rows = (await db.execute(text("""
        SELECT b.block_id, b.page, b.section, b.text, r.record_id, r.source_id, r.title, r.pub_year
        FROM kb_block b JOIN kb_document_version v ON v.version_id = b.version_id JOIN kb_document d ON d.document_id = v.document_id
        JOIN lit_record r ON r.record_id = d.record_id WHERE b.block_id = ANY(:ids)"""), {'ids': top})).mappings().all()
    by = {r['block_id']: {**dict(r), 'distance': dist.get(r['block_id'])} for r in rows}
    return [by[b] for b in top if b in by]


# ======================= 结构化证据 =======================
async def search_structured(db: AsyncSession, keywords: list[str], kind: str, limit: int = 8) -> list[dict]:
    """在推荐意见 / 研究发现里按多个关键词检索，按命中词数排序"""
    fn = KbwebDao.search_recommendations if kind == 'recommendation' else KbwebDao.search_findings
    key = 'rec_id' if kind == 'recommendation' else 'finding_id'
    hits: dict[int, dict] = {}
    for kw in [k for k in keywords if k and len(k) >= 3][:8]:
        for r in (await fn(db, {'q': kw, 'page_num': 1, 'page_size': 30}))['rows']:
            hits.setdefault(r[key], {**r, '_n': 0})['_n'] += 1
    return sorted(hits.values(), key=lambda r: -r['_n'])[:limit]


async def record_brief(db: AsyncSession, source_id: str) -> dict | None:
    rid = (await db.execute(text('SELECT record_id FROM lit_record WHERE source_id = :s'), {'s': str(source_id)})).scalar()
    return await KbwebDao.record_bundle(db, rid) if rid else None


async def resolve_sources(db: AsyncSession, source_ids: list[str]) -> list[dict]:
    if not source_ids:
        return []
    rows = (await db.execute(text("""
        SELECT r.record_id, r.source_id, r.title, r.pub_year, sd.design_label, EXISTS (SELECT 1 FROM kb_document d WHERE d.record_id = r.record_id) AS structured
        FROM lit_record r LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
        WHERE r.source_id = ANY(:s)"""), {'s': source_ids})).mappings().all()
    by = {r['source_id']: dict(r) for r in rows}
    return [by[s] for s in source_ids if s in by]


async def source_ids_by_record(db: AsyncSession, record_ids: list[int]) -> dict[int, str]:
    ids = [i for i in set(record_ids) if i]
    if not ids:
        return {}
    rows = (await db.execute(text('SELECT record_id, source_id FROM lit_record WHERE record_id = ANY(:i)'), {'i': ids})).all()
    return {r[0]: r[1] for r in rows}


_structured: set[int] | None = None


async def structured_record_ids(db: AsyncSession) -> set[int]:
    """已做全文结构化的题录（目前 40 篇），检索结果里据此提示模型去查全文"""
    global _structured  # noqa: PLW0603
    if _structured is None:
        _structured = set((await db.execute(text('SELECT record_id FROM kb_document'))).scalars().all())
    return _structured


# ======================= 概念关系（类知识图谱） =======================
async def find_concepts(db: AsyncSession, term: str, limit: int = 3) -> list[dict]:
    """按中文名 / 英文名 / 别名找概念：完全相同的排前面，其次是包含该词的（名称越短越靠前）"""
    t = (term or '').strip()
    if not t:
        return []
    rows = (await db.execute(text("""
        SELECT k.concept_id, k.concept_type, k.name_zh, k.name_en,
               CASE WHEN lower(k.name_zh) = lower(:t) OR lower(k.name_en) = lower(:t)
                         OR EXISTS (SELECT 1 FROM kb_concept_alias a WHERE a.concept_id = k.concept_id AND lower(a.alias) = lower(:t))
                    THEN 0 ELSE 1 END AS pri
        FROM kb_concept k
        WHERE k.name_zh ILIKE :kw OR k.name_en ILIKE :kw
           OR EXISTS (SELECT 1 FROM kb_concept_alias a WHERE a.concept_id = k.concept_id AND a.alias ILIKE :kw)
        ORDER BY pri, length(coalesce(k.name_zh, k.name_en)), k.concept_id LIMIT :n"""),
        {'t': t, 'kw': '%' + t.replace('%', r'\%').replace('_', r'\_') + '%', 'n': limit})).mappings().all()
    return [dict(r) for r in rows]


DESIGN_RANK = ['sr_ma', 'rct', 'nonrandomized_trial', 'cohort', 'case_control', 'diagnostic_accuracy',
               'cross_sectional', 'case_series', 'case_report', 'guideline', 'other', 'not_reported']


async def query_edges(db: AsyncSession, subject: str | None = None, outcome: str | None = None,
                      population: str | None = None, predicate: str | None = None, own_only: bool = True,
                      designs: list[str] | None = None, limit: int = 80) -> dict:
    """
    按 PICO 约束查通用关系表 kb_edge：主语（干预/暴露/检查）、宾语（结局）、人群各取最匹配的一个概念，
    沿 is_a 展开到全部下位概念后过滤；不取邻居再筛。own_only=True 时只要本研究结果和指南推荐（不要引用他人的结果）
    """
    resolved: dict[str, dict | None] = {}
    for slot, term in (('subject', subject), ('outcome', outcome), ('population', population)):
        found = await find_concepts(db, term, limit=1) if term else []
        resolved[slot] = found[0] if found else None
    if any(term and not resolved[slot] for slot, term in (('subject', subject), ('outcome', outcome),
                                                          ('population', population))):
        return {'resolved': resolved, 'rows': []}  # 有词但找不到概念：不放宽条件，交给全文检索

    def seed(slot: str) -> list[int]:
        return [resolved[slot]['concept_id']] if resolved[slot] else []

    rows = (await db.execute(text("""
        WITH RECURSIVE
        s(id) AS (SELECT unnest(CAST(:s AS bigint[])) UNION SELECT r.child_id FROM kb_concept_relation r JOIN s ON r.parent_id = s.id
                  WHERE r.rel_type = 'is_a' AND r.review_status <> 'rejected'),
        o(id) AS (SELECT unnest(CAST(:o AS bigint[])) UNION SELECT r.child_id FROM kb_concept_relation r JOIN o ON r.parent_id = o.id
                  WHERE r.rel_type = 'is_a' AND r.review_status <> 'rejected'),
        p(id) AS (SELECT unnest(CAST(:p AS bigint[])) UNION SELECT r.child_id FROM kb_concept_relation r JOIN p ON r.parent_id = p.id
                  WHERE r.rel_type = 'is_a' AND r.review_status <> 'rejected')
        SELECT e.edge_id, e.predicate, e.statement_role, e.design, e.context, e.qualifiers, e.trial_ids,
               coalesce(sc.name_zh, sc.name_en) AS subject, coalesce(oc.name_zh, oc.name_en) AS object,
               coalesce(cc.name_zh, cc.name_en) AS comparator, lr.source_id, lr.title, lr.pub_year,
               (SELECT string_agg(sp.quote, ' … ') FROM kb_span sp WHERE sp.span_id = ANY(e.span_ids)) AS quote
        FROM kb_edge e
        LEFT JOIN kb_concept sc ON sc.concept_id = e.subject_id
        LEFT JOIN kb_concept oc ON oc.concept_id = e.object_id
        LEFT JOIN kb_concept cc ON cc.concept_id = e.comparator_id
        JOIN kb_document_version v ON v.version_id = e.version_id
        JOIN kb_document d ON d.document_id = v.document_id
        JOIN lit_record lr ON lr.record_id = d.record_id
        WHERE (cardinality(CAST(:s AS bigint[])) = 0 OR e.subject_id IN (SELECT id FROM s))
          AND (cardinality(CAST(:o AS bigint[])) = 0 OR e.object_id IN (SELECT id FROM o))
          AND (cardinality(CAST(:p AS bigint[])) = 0 OR e.population_ids && ARRAY(SELECT id FROM p))
          AND (CAST(:pred AS text) IS NULL OR e.predicate = :pred)
          AND (NOT :own OR e.statement_role IN ('own_result', 'recommendation'))
          AND (CAST(:designs AS text[]) IS NULL OR e.design = ANY(CAST(:designs AS text[])))
        ORDER BY array_position(CAST(:rank AS text[]), e.design::text), lr.pub_year DESC NULLS LAST, e.edge_id
        LIMIT :n"""), {'s': seed('subject'), 'o': seed('outcome'), 'p': seed('population'), 'pred': predicate,
                       'own': own_only, 'designs': designs, 'rank': DESIGN_RANK, 'n': limit})).mappings().all()
    return {'resolved': resolved, 'rows': [dict(r) for r in rows]}


async def concept_evidence(db: AsyncSession, concept_id: int) -> dict | None:
    """一个概念在 40 篇结构化文献中的全部关联：研究发现、推荐、断言、人群纳排条件、上下位概念；按文献分组"""
    b = await KbwebDao.concept_bundle(db, concept_id)
    if not b:
        return None
    rows = [('发现', f) for f in b['findings']] + [('推荐', r) for r in b['recommendations']] + \
           [('断言', a) for a in b['assertions']] + [('人群条件', p) for p in b['populations']]
    srcs = await source_ids_by_record(db, [x['record_id'] for _, x in rows if x.get('record_id')])
    docs: dict[str, dict] = {}
    for kind, x in rows:
        sid = srcs.get(x.get('record_id'))
        if not sid:
            continue
        d = docs.setdefault(sid, {'source_id': sid, 'title': x.get('record_title') or x.get('guideline_title') or '', 'items': []})
        d['items'].append((kind, x))
    return {'concept': b['concept'], 'relations': b['relations'], 'docs': list(docs.values())}
