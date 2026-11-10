"""
循证知识库客户端只读查询。

数据分两层：lit_record（全部题录 + 研究设计分类 + 循证问题标签）与 kb_*（试点文档的结构化抽取）。
结构化事实只存在通用关系表 kb_edge（origin=finding/assertion/recommendation，同一研究结果/推荐的各行 group_uid 相同）；
本文的人群、组别、结局描述在 kb_document_version.profile。接口仍按“研究发现（含多个结果）/推荐（含条件、措施）”
的形状返回：一个 group_uid = 一条研究发现，finding_id 取该组最小的 edge_id，result_id = edge_id。
所有 SQL 都只读。
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 推荐强度 / 证据确信度的原文写法五花八门（"Strong recommendation"、"moderate-quality evidence"…），统一归到几档
STRENGTH_SQL = """CASE WHEN rc.strength_as_reported ILIKE '%strong%' THEN 'strong'
    WHEN rc.strength_as_reported ILIKE '%weak%' OR rc.strength_as_reported ILIKE '%conditional%' THEN 'weak'
    WHEN rc.strength_as_reported IS NULL THEN 'not_reported' ELSE 'other' END"""
CERTAINTY_SQL = """CASE WHEN rc.certainty_as_reported ILIKE '%very low%' THEN 'very_low'
    WHEN rc.certainty_as_reported ILIKE '%high%' THEN 'high'
    WHEN rc.certainty_as_reported ILIKE '%moderate%' THEN 'moderate'
    WHEN rc.certainty_as_reported ILIKE '%low%' THEN 'low'
    WHEN rc.certainty_as_reported IS NULL THEN 'not_reported' ELSE 'other' END"""

# 概念被结构化证据引用的位置（概念页和概念列表共用）：ref_id = group_uid；人群条件概念在研究发现里记为 population
CONCEPT_USE_SQL = """
    SELECT subject_id AS concept_id, CASE origin WHEN 'recommendation' THEN 'rec' ELSE origin END AS kind, group_uid::text AS ref_id
        FROM kb_edge WHERE subject_id IS NOT NULL
    UNION ALL SELECT object_id, CASE origin WHEN 'recommendation' THEN 'rec' ELSE origin END, group_uid::text FROM kb_edge WHERE object_id IS NOT NULL
    UNION ALL SELECT comparator_id, origin, group_uid::text FROM kb_edge WHERE comparator_id IS NOT NULL
    UNION ALL SELECT unnest(population_ids), CASE origin WHEN 'recommendation' THEN 'rec' ELSE 'population' END, group_uid::text FROM kb_edge
"""

# 研究发现的各行（一个数值一行），按组拼成“研究发现 + 结果列表”
EDGE_SELECT_SQL = """
    SELECT e.edge_id, e.group_uid::text AS group_uid, e.version_id, e.subject_id, e.object_id, e.comparator_id, e.predicate,
           e.statement_role, e.design, e.context, e.qualifiers, e.span_ids, e.review_status,
           COALESCE(cs.name_zh, cs.name_en) AS subject, COALESCE(co.name_zh, co.name_en) AS object,
           COALESCE(cc.name_zh, cc.name_en) AS comparator, r.record_id, r.title AS record_title, r.pub_year
    FROM kb_edge e
    LEFT JOIN kb_concept cs ON cs.concept_id = e.subject_id
    LEFT JOIN kb_concept co ON co.concept_id = e.object_id
    LEFT JOIN kb_concept cc ON cc.concept_id = e.comparator_id
    JOIN kb_document_version v ON v.version_id = e.version_id
    JOIN kb_document d ON d.document_id = v.document_id
    JOIN lit_record r ON r.record_id = d.record_id
"""
EDGE_FROM_SQL = EDGE_SELECT_SQL.split('FROM kb_edge e', 1)[1]

RECORD_BASE_SQL = """
    FROM lit_record r
    LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
    LEFT JOIN kb_document kd ON kd.record_id = r.record_id
"""

REC_SELECT_SQL = f"""
    SELECT rc.rec_id, rc.uid::text AS rec_uid, rc.guideline_id, rc.number_as_reported, rc.text_verbatim, rc.direction_wording,
           rc.strength_as_reported, rc.certainty_as_reported, {STRENGTH_SQL} AS strength, {CERTAINTY_SQL} AS certainty,
           rc.review_status, g.issuing_body, g.region, g.pub_date, r.record_id, r.title AS guideline_title, r.pub_year
    FROM kb_recommendation rc
    JOIN kb_guideline g ON g.guideline_id = rc.guideline_id
    LEFT JOIN kb_document_version v ON v.version_id = rc.version_id
    LEFT JOIN kb_document d ON d.document_id = v.document_id
    LEFT JOIN lit_record r ON r.record_id = d.record_id
"""


def _rows(result) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result]


def _like(q: str) -> str:
    return '%' + q.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'


def _snippet(txt: str | None, q: str | None, width: int = 240) -> str:
    if not txt:
        return ''
    i = txt.lower().find(q.lower()) if q else -1
    if i < 0:
        return txt[:width] + ('…' if len(txt) > width else '')
    s = max(0, i - width // 3)
    return ('…' if s > 0 else '') + txt[s:s + width] + ('…' if s + width < len(txt) else '')


async def _all(db: AsyncSession, sql: str, p: dict | None = None) -> list[dict]:
    return _rows(await db.execute(text(sql), p or {}))


async def _one(db: AsyncSession, sql: str, p: dict | None = None) -> dict | None:
    row = (await db.execute(text(sql), p or {})).first()
    return dict(row._mapping) if row else None


def _group(rows: list[dict], key: str) -> dict[Any, list[dict]]:
    out: dict[Any, list[dict]] = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def _j(v: Any) -> Any:
    """asyncpg 把 jsonb 当文本返回"""
    return json.loads(v) if isinstance(v, str) else (v or {})


async def _names(db: AsyncSession, ids: set) -> dict[int, str]:
    ids = {i for i in ids if isinstance(i, int)}
    if not ids:
        return {}
    rows = await _all(db, 'SELECT concept_id, COALESCE(name_zh, name_en) AS name FROM kb_concept WHERE concept_id = ANY(:ids)',
                      {'ids': list(ids)})
    return {r['concept_id']: r['name'] for r in rows}


async def _profiles(db: AsyncSession, vids: set) -> dict[int, dict]:
    if not vids:
        return {}
    rows = await _all(db, 'SELECT version_id, profile FROM kb_document_version WHERE version_id = ANY(:v)', {'v': list(vids)})
    out = {}
    for r in rows:
        p = _j(r['profile'])
        out[r['version_id']] = {
            'run_id': p.get('run_id'),
            'populations': {x['population_id']: x for x in p.get('populations') or []},
            'arms': {x['arm_id']: x for x in p.get('arms') or []},
            'outcomes': {x['outcome_id']: x for x in p.get('outcomes') or []},
        }
    return out


async def _span_rows(db: AsyncSession, ids: set) -> dict[int, dict]:
    if not ids:
        return {}
    rows = await _all(db, 'SELECT span_id, block_id, page, quote, quote_verified FROM kb_span WHERE span_id = ANY(:ids)',
                      {'ids': list(ids)})
    return {r['span_id']: r for r in rows}


def _spans(span_rows: dict[int, dict], ids: list | None, role: str = 'source') -> list[dict]:
    return sorted(({**span_rows[i], 'role': role} for i in ids or [] if i in span_rows),
                  key=lambda s: (s['page'] or 0, s['span_id']))


def _walk(obj: Any):
    """jsonb 中的全部对象（dict）"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk(x)


def _ids_in(obj: Any) -> set[int]:
    """jsonb 里键为 concept_id / *_concept_id 的全部值"""
    out: set[int] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (k == 'concept_id' or k.endswith('_concept_id')) and isinstance(v, int):
                out.add(v)
            else:
                out |= _ids_in(v)
    elif isinstance(obj, list):
        for x in obj:
            out |= _ids_in(x)
    return out


async def build_findings(db: AsyncSession, edges: list[dict]) -> list[dict]:
    """
    kb_edge 行（origin=finding）→ 研究发现：一个 group_uid 一条，结果按 edge_id 排列；人群、组别、结局的展示信息取自 profile
    """
    for e in edges:
        e['qualifiers'] = _j(e['qualifiers'])
    profiles = await _profiles(db, {e['version_id'] for e in edges})
    names = await _names(db, set().union(*(_ids_in(e['qualifiers']) for e in edges)) if edges else set())
    span_rows = await _span_rows(db, {s for e in edges for s in [*(e['span_ids'] or []), *(e['qualifiers'].get('adjustment_span_ids') or [])]})
    out = []
    for gid, rows in _group(edges, 'group_uid').items():
        rows.sort(key=lambda r: r['edge_id'])
        first, q = rows[0], rows[0]['qualifiers']
        prof = profiles.get(first['version_id'], {})
        pop = prof.get('populations', {}).get(q.get('population_ref')) or {}
        outcome = prof.get('outcomes', {}).get(q.get('outcome_ref')) or {}
        arms = prof.get('arms', {})
        plain = next((r for r in rows if not r['qualifiers'].get('arm_ref')), first)
        ex_id = q.get('exposure_concept_id') or plain['subject_id']
        cmp_id = q.get('comparator_concept_id') or plain['comparator_id']
        level = q.get('exposure_level') or {}
        adjustment = list(dict.fromkeys(q.get('adjustment_span_ids') or []))
        source_ids = list(dict.fromkeys(s for r in rows for s in r['span_ids'] or []))
        out.append({
            'finding_id': first['edge_id'], 'group_uid': gid, 'finding_type': q.get('finding_type') or 'other',
            'statement_role': first['statement_role'], 'study_id': pop.get('study_id'), 'population_id': q.get('population_ref'),
            'model_type': q.get('model'), 'pooled_k': q.get('pooled_k'), 'pooled_n': q.get('pooled_n'),
            'review_status': first['review_status'],
            'exposure_as_reported': q.get('exposure_text'), 'exposure_concept_id': ex_id,
            'exposure_concept': names.get(ex_id) or (plain['subject'] if ex_id == plain['subject_id'] else None),
            'exposure_level_low': level.get('low'), 'exposure_level_high': level.get('high'), 'exposure_level_unit': level.get('unit'),
            'comparator_as_reported': q.get('comparator_text'), 'comparator_concept_id': cmp_id,
            'comparator_concept': names.get(cmp_id) or (plain['comparator'] if cmp_id == plain['comparator_id'] else None),
            'exposure_arm_id': q.get('exposure_arm'), 'comparator_arm_id': q.get('comparator_arm'),
            'exposure_arm': (arms.get(q.get('exposure_arm')) or {}).get('label'),
            'comparator_arm': (arms.get(q.get('comparator_arm')) or {}).get('label'),
            'outcome_id': q.get('outcome_ref'), 'outcome_label': q.get('outcome_text'), 'outcome_concept_id': first['object_id'],
            'outcome_concept': first['object'], 'outcome_primacy': outcome.get('primacy'),
            'population_label': q.get('population_text') or pop.get('label'), 'population_n': pop.get('n_value'),
            'design_coarse': first['design'], 'study_name': None,
            'record_id': first['record_id'], 'record_title': first['record_title'], 'pub_year': first['pub_year'],
            'results': [{
                'finding_id': first['edge_id'], 'result_id': r['edge_id'], 'measure': r['qualifiers'].get('measure') or 'other',
                'category': r['qualifiers'].get('category'), 'arm': r['qualifiers'].get('arm'),
                'value_as_reported': r['qualifiers'].get('value_text') or '-', 'value': r['qualifiers'].get('value'),
                'ci_low': r['qualifiers'].get('ci_low'), 'ci_high': r['qualifiers'].get('ci_high'),
                'ci_level': r['qualifiers'].get('ci_level'), 'numerator': r['qualifiers'].get('numerator'),
                'denominator': r['qualifiers'].get('denominator'), 'unit': r['qualifiers'].get('unit'),
                'p_as_reported': r['qualifiers'].get('p'), 'derived': bool(r['qualifiers'].get('denominator_source')),
                'direction': r['qualifiers'].get('direction'), 'subject_concept_id': r['subject_id'], 'subject': r['subject'],
            } for r in rows if r['qualifiers'].get('measure') or r['qualifiers'].get('value_text')],
            'spans': _spans(span_rows, source_ids) + _spans(span_rows, adjustment, 'adjustment'),
        })
    return out


async def build_assertions(db: AsyncSession, edges: list[dict]) -> list[dict]:
    span_rows = await _span_rows(db, {s for e in edges for s in e['span_ids'] or []})
    out = []
    for e in edges:
        q = _j(e['qualifiers'])
        out.append({
            'assertion_id': e['edge_id'], 'predicate': q.get('predicate_as_extracted') or e['predicate'], 'context': e['context'],
            'statement_role': e['statement_role'], 'subject_concept_id': e['subject_id'], 'subject': e['subject'] or q.get('subject_text'),
            'object_concept_id': e['object_id'], 'object': e['object'] or q.get('object_text'),
            'record_id': e['record_id'], 'record_title': e['record_title'], 'spans': _spans(span_rows, e['span_ids']),
        })
    return out


class KbwebDao:
    # ======================= 首页统计 =======================
    @classmethod
    async def stats(cls, db: AsyncSession) -> dict:
        counts = await _one(db, """
            SELECT (SELECT count(*) FROM lit_record WHERE del_flag = '0') AS records,
                   (SELECT count(*) FROM lit_record WHERE del_flag = '0' AND pdf_path IS NOT NULL) AS fulltexts,
                   (SELECT count(*) FROM kb_document) AS structured_docs,
                   (SELECT count(*) FROM kb_guideline) AS guidelines,
                   (SELECT count(*) FROM kb_recommendation) AS recommendations,
                   (SELECT count(*) FROM kb_study) AS studies,
                   (SELECT count(DISTINCT group_uid) FROM kb_edge WHERE origin = 'finding') AS findings,
                   (SELECT count(*) FROM kb_edge WHERE origin = 'finding' AND qualifiers ? 'measure') AS results,
                   (SELECT count(*) FROM kb_edge) AS edges,
                   (SELECT count(*) FROM kb_concept) AS concepts,
                   (SELECT count(*) FROM kb_concept_alias) AS aliases,
                   (SELECT count(*) FROM kb_span) AS spans,
                   (SELECT count(*) FROM kb_block) AS blocks,
                   (SELECT count(DISTINCT record_id) FROM kb_record_question) AS tagged_records""")
        topic = await _one(db, 'SELECT topic_code, topic_name FROM lit_topic ORDER BY topic_id LIMIT 1')
        by_year = await _all(db, """SELECT pub_year AS key, count(*) AS n FROM lit_record
            WHERE del_flag = '0' AND pub_year >= 1990 GROUP BY 1 ORDER BY 1""")
        by_design = await _all(db, """SELECT design_label AS key, count(*) AS n FROM lit_study_design
            WHERE is_current GROUP BY 1 ORDER BY 2 DESC""")
        by_lang = await _all(db, "SELECT COALESCE(language, 'unknown') AS key, count(*) AS n FROM lit_record WHERE del_flag = '0' GROUP BY 1 ORDER BY 2 DESC")
        by_source = await _all(db, """SELECT COALESCE(NULLIF(source_db, ''), '未注明') AS key, count(*) AS n FROM lit_record
            WHERE del_flag = '0' GROUP BY 1 ORDER BY 2 DESC""")
        questions = await cls.question_list(db)
        by_doc_kind = await _all(db, 'SELECT doc_kind AS key, count(*) AS n FROM kb_document GROUP BY 1 ORDER BY 2 DESC')
        by_measure = await _all(db, """SELECT qualifiers->>'measure' AS key, count(*) AS n FROM kb_edge
            WHERE origin = 'finding' AND qualifiers ? 'measure' GROUP BY 1 ORDER BY 2 DESC""")
        by_finding_type = await _all(db, """SELECT qualifiers->>'finding_type' AS key, count(DISTINCT group_uid) AS n FROM kb_edge
            WHERE origin = 'finding' GROUP BY 1 ORDER BY 2 DESC""")
        top_concepts = await _all(db, f"""
            WITH u AS ({CONCEPT_USE_SQL})
            SELECT c.concept_id, c.concept_type, COALESCE(c.name_zh, c.name_en) AS name, c.name_en, count(DISTINCT u.kind || u.ref_id) AS n
            FROM u JOIN kb_concept c ON c.concept_id = u.concept_id
            GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40""")
        return {
            'counts': counts, 'topic': topic, 'by_year': by_year, 'by_design': by_design, 'by_lang': by_lang,
            'by_source': by_source, 'questions': questions, 'by_doc_kind': by_doc_kind, 'by_measure': by_measure,
            'by_finding_type': by_finding_type, 'top_concepts': top_concepts,
        }

    @classmethod
    async def question_list(cls, db: AsyncSession) -> list[dict]:
        return await _all(db, """
            SELECT q.eq_id, q.code, q.label,
                   (SELECT count(*) FROM kb_record_question x WHERE x.eq_id = q.eq_id) AS records,
                   (SELECT count(*) FROM kb_record_question x JOIN kb_document d ON d.record_id = x.record_id WHERE x.eq_id = q.eq_id) AS structured_docs
            FROM kb_evidence_question q WHERE q.status = 'active' ORDER BY q.code""")

    # ======================= 联想 =======================
    @classmethod
    async def suggest(cls, db: AsyncSession, q: str) -> list[dict]:
        p = {'kw': _like(q)}
        concepts = await _all(db, """
            SELECT DISTINCT ON (c.concept_id) 'concept' AS type, c.concept_id AS id, COALESCE(c.name_zh, c.name_en) AS term,
                   c.concept_type AS sub
            FROM kb_concept c LEFT JOIN kb_concept_alias a ON a.concept_id = c.concept_id
            WHERE c.name_zh ILIKE :kw OR c.name_en ILIKE :kw OR a.alias ILIKE :kw
            ORDER BY c.concept_id LIMIT 8""", p)
        records = await _all(db, """
            SELECT 'record' AS type, r.record_id AS id, r.title AS term, r.pub_year::text AS sub
            FROM lit_record r WHERE r.del_flag = '0' AND r.title ILIKE :kw
            ORDER BY (EXISTS (SELECT 1 FROM kb_document d WHERE d.record_id = r.record_id)) DESC, r.pub_year DESC NULLS LAST LIMIT 6""", p)
        return concepts + records

    # ======================= 检索：题录 =======================
    @classmethod
    async def search_records(cls, db: AsyncSession, f: dict) -> dict:
        where, p = ["r.del_flag = '0'"], {}
        if f.get('q'):
            where.append('(r.title ILIKE :kw OR r.abstract ILIKE :kw OR r.authors ILIKE :kw OR r.doi ILIKE :kw)')
            p['kw'] = _like(f['q'])
        if f.get('year_from'):
            where.append('r.pub_year >= :yf'); p['yf'] = f['year_from']
        if f.get('year_to'):
            where.append('r.pub_year <= :yt'); p['yt'] = f['year_to']
        if f.get('design'):
            where.append('sd.design_label = :design'); p['design'] = f['design']
        if f.get('lang'):
            where.append('r.language = :lang'); p['lang'] = f['lang']
        if f.get('eq'):
            where.append('EXISTS (SELECT 1 FROM kb_record_question x JOIN kb_evidence_question q ON q.eq_id = x.eq_id WHERE x.record_id = r.record_id AND q.code = :eq)')
            p['eq'] = f['eq']
        if f.get('structured'):
            where.append('kd.document_id IS NOT NULL')
        if f.get('has_pdf'):
            where.append('r.pdf_path IS NOT NULL')
        w = ' AND '.join(where)
        base = f'{RECORD_BASE_SQL} WHERE {w}'
        # 关键词要全表扫摘要（约 0.7s/次），总数和全部分面放进一个物化 CTE 里只扫一遍
        agg = _group(await _all(db, f"""
            WITH m AS MATERIALIZED (SELECT r.record_id, r.pub_year, r.language, sd.design_label, kd.document_id, r.pdf_path IS NOT NULL AS has_pdf {base})
            SELECT 'total' AS facet, NULL AS key, NULL AS label, count(*) AS n FROM m
            UNION ALL SELECT 'structured', NULL, NULL, count(*) FROM m WHERE document_id IS NOT NULL
            UNION ALL SELECT 'has_pdf', NULL, NULL, count(*) FROM m WHERE has_pdf
            UNION ALL SELECT 'year', pub_year::text, NULL, count(*) FROM m WHERE pub_year IS NOT NULL GROUP BY pub_year
            UNION ALL SELECT 'design', design_label, NULL, count(*) FROM m WHERE design_label IS NOT NULL GROUP BY design_label
            UNION ALL SELECT 'lang', language, NULL, count(*) FROM m WHERE language IS NOT NULL GROUP BY language
            UNION ALL SELECT 'eq', q.code, q.label, count(*) FROM m JOIN kb_record_question x ON x.record_id = m.record_id
                JOIN kb_evidence_question q ON q.eq_id = x.eq_id GROUP BY q.code, q.label""", p), 'facet')
        total = agg['total'][0]['n']
        order = {
            'year_desc': 'r.pub_year DESC NULLS LAST, r.record_id',
            'year_asc': 'r.pub_year ASC NULLS LAST, r.record_id',
        }.get(f.get('sort'), "(kd.document_id IS NOT NULL) DESC, (r.title ILIKE :kw) DESC, r.pub_year DESC NULLS LAST, r.record_id" if f.get('q')
              else '(kd.document_id IS NOT NULL) DESC, r.pub_year DESC NULLS LAST, r.record_id')
        rows = await _all(db, f"""
            SELECT r.record_id, r.title, r.authors, r.pub_year, r.language, r.source_db, r.doi, r.pmid,
                   r.abstract, r.pdf_path IS NOT NULL AS has_pdf, sd.design_label, kd.document_id, kd.doc_kind,
                   (SELECT array_agg(q.code ORDER BY q.code) FROM kb_record_question x JOIN kb_evidence_question q ON q.eq_id = x.eq_id
                     WHERE x.record_id = r.record_id) AS questions
            {base} ORDER BY {order} LIMIT :lim OFFSET :off""", {**p, 'lim': f['page_size'], 'off': (f['page_num'] - 1) * f['page_size']})
        for r in rows:
            r['snippet'] = _snippet(r.pop('abstract'), f.get('q'))
            r['authors'] = (r['authors'] or '')[:160]

        def facet(name: str, by_n: bool = True) -> list[dict]:
            items = [{'key': x['key'], 'label': x['label'], 'n': x['n']} for x in agg.get(name, [])]
            return sorted(items, key=lambda x: -x['n']) if by_n else sorted(items, key=lambda x: x['key'], reverse=True)

        facets = {
            'year': [{'key': int(x['key']), 'n': x['n']} for x in facet('year', by_n=False)],
            'design': facet('design'),
            'lang': facet('lang'),
            'eq': sorted(facet('eq'), key=lambda x: x['key']),
            'structured': agg['structured'][0]['n'],
            'has_pdf': agg['has_pdf'][0]['n'],
        }
        return {'rows': rows, 'total': total, 'facets': facets}

    # ======================= 检索：推荐意见 =======================
    @classmethod
    async def search_recommendations(cls, db: AsyncSession, f: dict) -> dict:
        where, p = ['TRUE'], {}
        if f.get('q'):
            where.append("""(rc.text_verbatim ILIKE :kw OR EXISTS (SELECT 1 FROM kb_edge e WHERE e.group_uid = rc.uid AND (
                    e.qualifiers->>'action_text' ILIKE :kw
                    OR EXISTS (SELECT 1 FROM kb_concept k WHERE (k.concept_id = e.subject_id OR k.concept_id = ANY(e.population_ids))
                               AND (k.name_zh ILIKE :kw OR k.name_en ILIKE :kw)))))""")
            p['kw'] = _like(f['q'])
        if f.get('guideline_id'):
            where.append('rc.guideline_id = :gid'); p['gid'] = f['guideline_id']
        if f.get('strength'):
            where.append(f'({STRENGTH_SQL}) = :st'); p['st'] = f['strength']
        if f.get('certainty'):
            where.append(f'({CERTAINTY_SQL}) = :ct'); p['ct'] = f['certainty']
        if f.get('action_type'):
            where.append("EXISTS (SELECT 1 FROM kb_edge e WHERE e.group_uid = rc.uid AND e.qualifiers->>'action_type' = :at)")
            p['at'] = f['action_type']
        if f.get('concept_id'):
            where.append("""EXISTS (SELECT 1 FROM kb_edge e WHERE e.group_uid = rc.uid
                AND (e.subject_id = :cid OR e.population_ids @> ARRAY[CAST(:cid AS bigint)]))""")
            p['cid'] = f['concept_id']
        w = ' AND '.join(where)
        base = f'FROM kb_recommendation rc JOIN kb_guideline g ON g.guideline_id = rc.guideline_id WHERE {w}'
        total = (await db.execute(text(f'SELECT count(*) {base}'), p)).scalar()
        rows = await _all(db, f"""{REC_SELECT_SQL} WHERE {w}
            ORDER BY rc.guideline_id, rc.rec_id
            LIMIT :lim OFFSET :off""", {**p, 'lim': f['page_size'], 'off': (f['page_num'] - 1) * f['page_size']})
        await cls.attach_rec_details(db, rows)
        facets = {
            'guideline': await _all(db, f"""SELECT rc.guideline_id AS key, r.title AS label, count(*) AS n {base.replace('WHERE', '''LEFT JOIN kb_guideline_document gd ON gd.guideline_id = g.guideline_id
                LEFT JOIN kb_document d ON d.document_id = gd.document_id LEFT JOIN lit_record r ON r.record_id = d.record_id WHERE''', 1)} GROUP BY 1, 2 ORDER BY 1""", p),
            'strength': await _all(db, f'SELECT {STRENGTH_SQL} AS key, count(*) AS n {base} GROUP BY 1 ORDER BY 2 DESC', p),
            'certainty': await _all(db, f'SELECT {CERTAINTY_SQL} AS key, count(*) AS n {base} GROUP BY 1 ORDER BY 2 DESC', p),
            'action_type': await _all(db, f"""SELECT e.qualifiers->>'action_type' AS key, count(DISTINCT rc.rec_id) AS n
                {base.replace('WHERE', 'JOIN kb_edge e ON e.group_uid = rc.uid WHERE', 1)} AND e.qualifiers ? 'action_type'
                GROUP BY 1 ORDER BY 2 DESC""", p),
        }
        return {'rows': rows, 'total': total, 'facets': facets}

    @classmethod
    async def attach_rec_details(cls, db: AsyncSession, recs: list[dict]) -> None:
        """给推荐意见挂上适用条件、行动（kb_edge，group_uid = 推荐 uid）、引用文献和原文溯源"""
        ids = [r['rec_id'] for r in recs]
        if not ids:
            return
        p = {'ids': ids}
        edges = await _all(db, """
            SELECT e.edge_id, e.group_uid::text AS rec_uid, e.subject_id, COALESCE(k.name_zh, k.name_en) AS target_concept, e.qualifiers
            FROM kb_edge e LEFT JOIN kb_concept k ON k.concept_id = e.subject_id
            WHERE e.origin = 'recommendation' AND e.group_uid IN (SELECT uid FROM kb_recommendation WHERE rec_id = ANY(:ids))
            ORDER BY e.edge_id""", p)
        for e in edges:
            e['qualifiers'] = _j(e['qualifiers'])
        fields = {r['field_code']: r['label_zh'] for r in await _all(db, 'SELECT field_code, label_zh FROM kb_rule_input_field')}
        names = await _names(db, set().union(*(_ids_in(e['qualifiers'].get('conditions')) for e in edges)) if edges else set())
        by_uid = _group(edges, 'rec_uid')
        conds, actions = {}, {}
        for r in recs:
            rows = by_uid.get(r['rec_uid'], [])
            conds[r['rec_id']] = [{
                'rec_id': r['rec_id'], 'rule_field_code': c.get('rule_field_code'), 'field_label': fields.get(c.get('rule_field_code')),
                'concept_id': c.get('concept_id'), 'concept': names.get(c.get('concept_id')) or c.get('text'),
                'qualifier': c.get('qualifier'), 'value_low': c.get('value_low'), 'value_high': c.get('value_high'),
                'value_unit': c.get('value_unit'), 'value_as_reported': c.get('value_as_reported'), 'or_group': c.get('or_group'),
            } for c in (rows[0]['qualifiers'].get('conditions') or [] if rows else [])]
            actions[r['rec_id']] = sorted(({  # 各行已按 edge_id 排序，sorted 稳定，只按分期调整
                'rec_id': r['rec_id'], 'action_type': e['qualifiers']['action_type'], 'target_concept_id': e['subject_id'],
                'target_concept': e['target_concept'] or e['qualifiers'].get('target_text'),
                'interval_low': e['qualifiers'].get('interval_low'), 'interval_high': e['qualifiers'].get('interval_high'),
                'interval_unit': e['qualifiers'].get('interval_unit'), 'anchor': e['qualifiers'].get('anchor'),
                'phase_no': e['qualifiers'].get('phase_no'), 'text_as_reported': e['qualifiers'].get('action_text') or '',
            } for e in rows if e['qualifiers'].get('action_type')), key=lambda a: a['phase_no'] or 0)
        cites = _group(await _all(db, """
            SELECT c.rec_id, c.seq, c.ref_label, c.match_status, c.citation_basis, c.matched_record_id,
                   r.title AS matched_title, r.pub_year AS matched_year, sd.design_label AS matched_design, kd.document_id IS NOT NULL AS matched_structured
            FROM kb_rec_citation c
            LEFT JOIN lit_record r ON r.record_id = c.matched_record_id
            LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
            LEFT JOIN kb_document kd ON kd.record_id = r.record_id
            WHERE c.rec_id = ANY(:ids) ORDER BY c.rec_id, c.seq""", p), 'rec_id')
        spans = await cls.spans_for(db, 'recommendation', ids)
        for r in recs:
            r['conditions'] = conds.get(r['rec_id'], [])
            r['actions'] = actions.get(r['rec_id'], [])
            r['citations'] = cites.get(r['rec_id'], [])
            r['spans'] = spans.get(r['rec_id'], [])

    # ======================= 检索：研究发现 =======================
    @classmethod
    def _finding_where(cls, f: dict) -> tuple[str, str, dict]:
        """
        :return: (行级条件, 组级条件 HAVING, 参数)。研究设计/陈述角色/类型/文献在组内相同；关键词、概念、效应量只要组内有一行满足
        """
        where, having, p = ["e.origin = 'finding'"], [], {}
        if f.get('q'):
            having.append("""bool_or(e.qualifiers->>'exposure_text' ILIKE :kw OR e.qualifiers->>'comparator_text' ILIKE :kw
                OR e.qualifiers->>'outcome_text' ILIKE :kw OR cs.name_zh ILIKE :kw OR cs.name_en ILIKE :kw
                OR co.name_zh ILIKE :kw OR co.name_en ILIKE :kw OR r.title ILIKE :kw)""")
            p['kw'] = _like(f['q'])
        if f.get('finding_type'):
            where.append("e.qualifiers->>'finding_type' = :ft"); p['ft'] = f['finding_type']
        if f.get('measure'):
            having.append("bool_or(e.qualifiers->>'measure' = :ms)"); p['ms'] = f['measure']
        if f.get('design'):
            where.append('e.design = :dc'); p['dc'] = f['design']
        if f.get('statement_role'):
            where.append('e.statement_role = :sr'); p['sr'] = f['statement_role']
        if f.get('concept_id'):
            having.append("""bool_or(e.subject_id = :cid OR e.object_id = :cid OR e.comparator_id = :cid
                OR e.qualifiers->>'exposure_concept_id' = CAST(:cid AS text) OR e.qualifiers->>'comparator_concept_id' = CAST(:cid AS text)
                OR e.qualifiers->>'reference_standard_concept_id' = CAST(:cid AS text))""")
            p['cid'] = f['concept_id']
        if f.get('record_id'):
            where.append('r.record_id = :rid'); p['rid'] = f['record_id']
        if f.get('eq'):
            where.append('EXISTS (SELECT 1 FROM kb_record_question x JOIN kb_evidence_question q ON q.eq_id = x.eq_id WHERE x.record_id = r.record_id AND q.code = :eq)')
            p['eq'] = f['eq']
        return ' AND '.join(where), (' HAVING ' + ' AND '.join(having)) if having else '', p

    @classmethod
    async def search_findings(cls, db: AsyncSession, f: dict) -> dict:
        w, having, p = cls._finding_where(f)
        groups = f"""SELECT e.group_uid, min(e.edge_id) AS fid, max(r.pub_year) AS pub_year
            FROM kb_edge e {EDGE_FROM_SQL} WHERE {w} GROUP BY e.group_uid {having}"""
        total = (await db.execute(text(f'SELECT count(*) FROM ({groups}) g'), p)).scalar()
        page = await _all(db, f"""{groups} ORDER BY pub_year DESC NULLS LAST, fid LIMIT :lim OFFSET :off""",
                          {**p, 'lim': f['page_size'], 'off': (f['page_num'] - 1) * f['page_size']})
        edges = await _all(db, f'{EDGE_SELECT_SQL} WHERE e.group_uid = ANY(:g) ORDER BY e.edge_id',
                           {'g': [x['group_uid'] for x in page]}) if page else []
        by_id = {x['finding_id']: x for x in await build_findings(db, edges)}
        rows = [by_id[x['fid']] for x in page if x['fid'] in by_id]
        matched = f'FROM kb_edge e WHERE e.group_uid IN (SELECT group_uid FROM ({groups}) g)'
        facets = {
            'finding_type': await _all(db, f"SELECT e.qualifiers->>'finding_type' AS key, count(DISTINCT e.group_uid) AS n {matched} GROUP BY 1 ORDER BY 2 DESC", p),
            'design': await _all(db, f'SELECT e.design AS key, count(DISTINCT e.group_uid) AS n {matched} GROUP BY 1 ORDER BY 2 DESC', p),
            'statement_role': await _all(db, f'SELECT e.statement_role AS key, count(DISTINCT e.group_uid) AS n {matched} GROUP BY 1 ORDER BY 2 DESC', p),
            'measure': await _all(db, f"""SELECT e.qualifiers->>'measure' AS key, count(DISTINCT e.group_uid) AS n {matched}
                AND e.qualifiers ? 'measure' GROUP BY 1 ORDER BY 2 DESC""", p),
        }
        return {'rows': rows, 'total': total, 'facets': facets}

    @classmethod
    async def findings_of_version(cls, db: AsyncSession, vid: int) -> list[dict]:
        edges = await _all(db, f"{EDGE_SELECT_SQL} WHERE e.version_id = :vid AND e.origin = 'finding' ORDER BY e.edge_id", {'vid': vid})
        return sorted(await build_findings(db, edges), key=lambda x: x['finding_id'])

    # ======================= 检索：原文段落 =======================
    @classmethod
    async def search_passages(cls, db: AsyncSession, f: dict) -> dict:
        where, p = ["b.block_type IN ('paragraph', 'list_item', 'table', 'caption')"], {}
        if f.get('q'):
            where.append('b.text ILIKE :kw'); p['kw'] = _like(f['q'])
        if f.get('block_type'):
            where.append('b.block_type = :bt'); p['bt'] = f['block_type']
        if f.get('record_id'):
            where.append('r.record_id = :rid'); p['rid'] = f['record_id']
        w = ' AND '.join(where)
        base = f"""FROM kb_block b JOIN kb_document_version v ON v.version_id = b.version_id
            JOIN kb_document d ON d.document_id = v.document_id JOIN lit_record r ON r.record_id = d.record_id WHERE {w}"""
        total = (await db.execute(text(f'SELECT count(*) {base}'), p)).scalar()
        rows = await _all(db, f"""
            SELECT b.block_id, b.block_type, b.page, b.section, b.text, r.record_id, r.title AS record_title, r.pub_year,
                   (SELECT count(*) FROM kb_span s WHERE s.block_id = b.block_id) AS span_count
            {base} ORDER BY r.record_id, b.seq LIMIT :lim OFFSET :off""", {**p, 'lim': f['page_size'], 'off': (f['page_num'] - 1) * f['page_size']})
        for r in rows:
            r['snippet'] = _snippet(r.pop('text'), f.get('q'), 320)
        facets = {
            'block_type': await _all(db, f'SELECT b.block_type AS key, count(*) AS n {base} GROUP BY 1 ORDER BY 2 DESC', p),
            'record': await _all(db, f'SELECT r.record_id AS key, r.title AS label, count(*) AS n {base} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15', p),
        }
        return {'rows': rows, 'total': total, 'facets': facets}

    # ======================= 溯源片段 =======================
    @classmethod
    async def spans_for(cls, db: AsyncSession, entity_type: str, ids: list[int]) -> dict[int, list[dict]]:
        if not ids:
            return {}
        rows = await _all(db, """
            SELECT sl.entity_id, sl.role, s.span_id, s.block_id, s.page, s.quote, s.quote_verified
            FROM kb_span_link sl JOIN kb_span s ON s.span_id = sl.span_id
            WHERE sl.entity_type = :t AND sl.entity_id = ANY(:ids) ORDER BY sl.entity_id, s.page, s.span_id""", {'t': entity_type, 'ids': ids})
        return _group(rows, 'entity_id')

    # ======================= 文献详情 =======================
    @classmethod
    async def record_bundle(cls, db: AsyncSession, record_id: int) -> dict | None:
        rec = await _one(db, """
            SELECT r.record_id, r.source_id, r.title, r.authors, r.pub_year, r.abstract, r.language, r.doi, r.pmid, r.source_db,
                   r.pdf_path IS NOT NULL AS has_pdf, sd.design_label, sd.design_code, sd.method AS design_method, sd.confidence AS design_confidence,
                   sd.rationale AS design_rationale, sd.review_status AS design_review_status
            FROM lit_record r LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
            WHERE r.record_id = :id AND r.del_flag = '0'""", {'id': record_id})
        if not rec:
            return None
        rec['questions'] = await _all(db, """SELECT q.code, q.label, x.method, x.confidence FROM kb_record_question x
            JOIN kb_evidence_question q ON q.eq_id = x.eq_id WHERE x.record_id = :id ORDER BY q.code""", {'id': record_id})
        out: dict[str, Any] = {'record': rec, 'kb': None}
        doc = await _one(db, """
            SELECT d.document_id, d.doc_kind, d.subject_scope, d.body_language, d.retraction_status, d.evidence_layer, d.triage_uncertainty,
                   v.version_id, v.page_count, v.parse_method, v.source_format, v.text_layer_ok
            FROM kb_document d LEFT JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current
            WHERE d.record_id = :id""", {'id': record_id})
        if not doc:
            return out
        vid, did = doc['version_id'], doc['document_id']
        studies = await _all(db, """
            SELECT s.study_id, s.study_name, s.design_coarse, s.design_as_reported, s.design_uncertainty, s.registration_ids, s.countries,
                   s.n_centers, s.period_as_reported, s.data_source, s.parent_study_name, s.unit_of_analysis_default, sr.report_role
            FROM kb_study_report sr JOIN kb_study s ON s.study_id = sr.study_id WHERE sr.document_id = :did ORDER BY s.study_id""", {'did': did})
        sids = [s['study_id'] for s in studies]
        s_spans = await cls.spans_for(db, 'study', sids)
        for s in studies:
            s['spans'] = s_spans.get(s['study_id'], [])
        # 人群、组别、结局：本文描述（profile），概念名现查
        prof = _j((await _one(db, 'SELECT profile FROM kb_document_version WHERE version_id = :vid', {'vid': vid}) or {}).get('profile'))
        names = await _names(db, _ids_in(prof))
        span_rows = await _span_rows(db, {s for x in _walk(prof) for s in x.get('span_ids') or []})

        def with_concepts(items: list[dict], role: str) -> list[dict]:
            for x in items:
                if 'concept_id' in x or 'text' in x:
                    x['concept'] = names.get(x.get('concept_id')) or x.get('text')
                x['spans'] = _spans(span_rows, x.pop('span_ids', None), role)
            return items

        populations = prof.get('populations') or []
        for x in populations:
            x['conditions'] = with_concepts(x.get('conditions') or [], 'eligibility')
        with_concepts(populations, 'population_definition')
        arms = prof.get('arms') or []
        for a in arms:
            a['interventions'] = with_concepts(a.get('interventions') or [], 'source')
        with_concepts(arms, 'source')
        outcomes = sorted(with_concepts(prof.get('outcomes') or [], 'definition'),
                          key=lambda o: {'primary': 0, 'secondary': 1}.get(o.get('primacy'), 2))
        findings = await cls.findings_of_version(db, vid)
        assertions = await build_assertions(db, await _all(
            db, f"{EDGE_SELECT_SQL} WHERE e.version_id = :vid AND e.origin = 'assertion' ORDER BY e.edge_id", {'vid': vid}))
        definitions = await _all(db, """
            SELECT t.definition_id, t.concept_id, COALESCE(k.name_zh, k.name_en) AS concept, t.definition_variant_id, dv.label AS variant_label
            FROM kb_term_definition t LEFT JOIN kb_concept k ON k.concept_id = t.concept_id
            LEFT JOIN kb_definition_variant dv ON dv.variant_id = t.definition_variant_id
            WHERE t.version_id = :vid ORDER BY t.definition_id""", {'vid': vid})
        d_spans = await cls.spans_for(db, 'definition', [x['definition_id'] for x in definitions])
        for x in definitions:
            x['spans'] = d_spans.get(x['definition_id'], [])
        guideline = await _one(db, """
            SELECT g.guideline_id, g.issuing_body, g.region, g.version_label, g.pub_date, g.fulltext_source, g.supersedes_as_reported, gd.role, gd.lang
            FROM kb_guideline_document gd JOIN kb_guideline g ON g.guideline_id = gd.guideline_id WHERE gd.document_id = :did""", {'did': did})
        recs: list[dict] = []
        if guideline:
            recs = await _all(db, f"""{REC_SELECT_SQL} WHERE rc.guideline_id = :gid ORDER BY rc.rec_id""",
                {'gid': guideline['guideline_id']})
            await cls.attach_rec_details(db, recs)
        concepts, concept_relations = await cls._doc_concepts(db, vid, prof, findings, assertions, definitions, recs)
        cited_by = await _all(db, f"""{REC_SELECT_SQL} WHERE rc.rec_id IN (SELECT rec_id FROM kb_rec_citation WHERE matched_record_id = :id)
            ORDER BY rc.guideline_id, rc.rec_id""", {'id': record_id})
        issues = await _one(db, """
            SELECT count(*) AS total, count(*) FILTER (WHERE NOT resolved) AS open
            FROM kb_validator_issue i
            WHERE i.run_id IN (SELECT CAST(:run AS bigint) UNION SELECT prefill_run_id FROM kb_recommendation WHERE version_id = :vid)""",
            {'vid': vid, 'run': prof.get('run_id')})
        out['kb'] = {
            'document': doc, 'studies': studies, 'populations': populations, 'arms': arms, 'outcomes': outcomes,
            'findings': findings, 'assertions': assertions, 'definitions': definitions, 'guideline': guideline,
            'recommendations': recs, 'issues': issues, 'concepts': concepts, 'concept_relations': concept_relations,
        }
        out['cited_by'] = cited_by
        return out

    @classmethod
    async def _doc_concepts(cls, db: AsyncSession, vid: int, prof: dict, findings, assertions, definitions, recs) -> tuple[dict, list]:
        """本文抽取用到的全部概念（图谱节点着色用）+ 这些概念之间的已知关系"""
        ids = _ids_in(prof)
        ids |= {f[k] for f in findings for k in ('exposure_concept_id', 'comparator_concept_id', 'outcome_concept_id')}
        ids |= {r['subject_concept_id'] for f in findings for r in f['results']}
        ids |= {a[k] for a in assertions for k in ('subject_concept_id', 'object_concept_id')}
        ids |= {x['concept_id'] for x in definitions}
        ids |= {c['concept_id'] for r in recs for c in r['conditions']} | {a['target_concept_id'] for r in recs for a in r['actions']}
        ids.discard(None)
        rels = await _all(db, """
            SELECT r.relation_id, r.parent_id, r.child_id, r.rel_type, r.review_status
            FROM kb_concept_relation r
            WHERE r.review_status IS DISTINCT FROM 'rejected' AND ((r.parent_id = ANY(:ids) AND r.child_id = ANY(:ids))
               OR EXISTS (SELECT 1 FROM kb_span_link sl JOIN kb_span s ON s.span_id = sl.span_id
                          WHERE sl.entity_type = 'concept_relation' AND sl.entity_id = r.relation_id AND s.version_id = :vid))
            ORDER BY r.relation_id""", {'ids': list(ids), 'vid': vid})
        ids |= {r['parent_id'] for r in rels} | {r['child_id'] for r in rels}
        r_spans = await cls.spans_for(db, 'concept_relation', [r['relation_id'] for r in rels])
        for r in rels:
            r['spans'] = r_spans.get(r['relation_id'], [])
        rows = await _all(db, 'SELECT concept_id, concept_type, name_zh, name_en FROM kb_concept WHERE concept_id = ANY(:ids)', {'ids': list(ids)})
        return {r['concept_id']: r for r in rows}, rels

    @classmethod
    async def record_fulltext(cls, db: AsyncSession, record_id: int) -> dict | None:
        doc = await _one(db, """
            SELECT r.source_id, v.version_id FROM lit_record r JOIN kb_document d ON d.record_id = r.record_id
            JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current WHERE r.record_id = :id""", {'id': record_id})
        if not doc:
            return None
        blocks = await _all(db, """
            SELECT block_id, seq, page, section, block_type, heading_level, text, extra->>'html' AS html,
                   extra->>'img_path' AS img_path, extra->>'kind' AS kind
            FROM kb_block WHERE version_id = :vid ORDER BY seq""", {'vid': doc['version_id']})
        # 片段被谁引用：kb_span_link（研究、推荐条款、定义、概念关系）+ kb_edge（研究发现、断言、推荐措施）+ profile（人群等）
        spans = await _all(db, """
            SELECT s.span_id, s.block_id, s.char_start, s.char_end, s.quote_verified,
                   array_remove(array_agg(DISTINCT sl.entity_type) || array_agg(DISTINCT e.origin), NULL) AS entity_types
            FROM kb_span s LEFT JOIN kb_span_link sl ON sl.span_id = s.span_id
            LEFT JOIN kb_edge e ON e.version_id = s.version_id
                AND (s.span_id = ANY(e.span_ids) OR e.qualifiers->'adjustment_span_ids' @> to_jsonb(s.span_id))
            WHERE s.version_id = :vid GROUP BY s.span_id ORDER BY s.block_id, s.char_start""", {'vid': doc['version_id']})
        prof = _j((await _one(db, 'SELECT profile FROM kb_document_version WHERE version_id = :vid', {'vid': doc['version_id']}) or {}).get('profile'))
        kinds: dict[int, set[str]] = {}
        for key, kind in (('populations', 'population'), ('arms', 'arm'), ('outcomes', 'outcome')):
            for x in prof.get(key) or []:
                for s in x.get('span_ids') or []:
                    kinds.setdefault(s, set()).add(kind)
                for sub, sub_kind in (('conditions', 'population_condition'), ('interventions', 'arm_intervention')):
                    for c in x.get(sub) or []:
                        for s in c.get('span_ids') or []:
                            kinds.setdefault(s, set()).add(sub_kind)
        for s in spans:
            s['entity_types'] = sorted(set(s['entity_types'] or []) | kinds.get(s['span_id'], set())) or [None]
        return {'blocks': blocks, 'spans': spans}

    @classmethod
    async def record_source(cls, db: AsyncSession, record_id: int) -> dict | None:
        return await _one(db, "SELECT source_id, pdf_path FROM lit_record WHERE record_id = :id AND del_flag = '0'", {'id': record_id})

    # ======================= 概念 =======================
    @classmethod
    async def concept_list(cls, db: AsyncSession, f: dict) -> dict:
        where, p = ['TRUE'], {}
        if f.get('q'):
            where.append('(c.name_zh ILIKE :kw OR c.name_en ILIKE :kw OR EXISTS (SELECT 1 FROM kb_concept_alias a WHERE a.concept_id = c.concept_id AND a.alias ILIKE :kw))')
            p['kw'] = _like(f['q'])
        if f.get('concept_type'):
            where.append('c.concept_type = :ct'); p['ct'] = f['concept_type']
        if not f.get('include_unused'):
            where.append('u.n > 0')
        w = ' AND '.join(where)
        cte = f"""WITH use AS ({CONCEPT_USE_SQL}),
            u AS (SELECT concept_id, count(DISTINCT ref_id) FILTER (WHERE kind = 'rec') AS recs,
                         count(DISTINCT ref_id) FILTER (WHERE kind = 'finding') AS findings,
                         count(DISTINCT kind || ref_id) AS n FROM use GROUP BY concept_id)"""
        base = f'FROM kb_concept c LEFT JOIN u ON u.concept_id = c.concept_id WHERE {w}'
        total = (await db.execute(text(f'{cte} SELECT count(*) {base}'), p)).scalar()
        rows = await _all(db, f"""{cte}
            SELECT c.concept_id, c.concept_type, c.name_zh, c.name_en, c.code, COALESCE(u.recs, 0) AS recs, COALESCE(u.findings, 0) AS findings,
                   COALESCE(u.n, 0) AS n, (SELECT count(*) FROM kb_concept_alias a WHERE a.concept_id = c.concept_id) AS aliases
            {base} ORDER BY COALESCE(u.n, 0) DESC, c.name_zh NULLS LAST, c.name_en LIMIT :lim OFFSET :off""",
            {**p, 'lim': f['page_size'], 'off': (f['page_num'] - 1) * f['page_size']})
        types = await _all(db, f'{cte} SELECT c.concept_type AS key, count(*) AS n {base.replace("AND c.concept_type = :ct", "")} GROUP BY 1 ORDER BY 2 DESC', p)
        return {'rows': rows, 'total': total, 'facets': {'concept_type': types}}

    @classmethod
    async def concept_bundle(cls, db: AsyncSession, concept_id: int) -> dict | None:
        c = await _one(db, """SELECT concept_id, concept_type, code, name_zh, name_en, status FROM kb_concept WHERE concept_id = :id""", {'id': concept_id})
        if not c:
            return None
        p = {'id': concept_id}
        c['aliases'] = await _all(db, 'SELECT alias, lang, source FROM kb_concept_alias WHERE concept_id = :id ORDER BY lang, alias', p)
        # 审核拒绝的关系不展示（多为已拆分替换或抽取错误）；带出来源和备注，便于标注“无库内原文”的人工补录关系
        relations = await _all(db, """
            SELECT 'parent' AS dir, r.relation_id, r.rel_type, r.review_status, r.source, r.note,
                   k.concept_id, k.concept_type, COALESCE(k.name_zh, k.name_en) AS name
            FROM kb_concept_relation r JOIN kb_concept k ON k.concept_id = r.parent_id
            WHERE r.child_id = :id AND r.review_status IS DISTINCT FROM 'rejected'
            UNION ALL
            SELECT 'child', r.relation_id, r.rel_type, r.review_status, r.source, r.note,
                   k.concept_id, k.concept_type, COALESCE(k.name_zh, k.name_en)
            FROM kb_concept_relation r JOIN kb_concept k ON k.concept_id = r.child_id
            WHERE r.parent_id = :id AND r.review_status IS DISTINCT FROM 'rejected'
            ORDER BY rel_type, name""", p)
        rel_spans = _group(await _all(db, """
            SELECT sl.entity_id, s.span_id, s.block_id, s.page, s.quote, s.quote_verified, r.record_id, r.source_id
            FROM kb_span_link sl JOIN kb_span s ON s.span_id = sl.span_id
            JOIN kb_document_version v ON v.version_id = s.version_id JOIN kb_document d ON d.document_id = v.document_id
            JOIN lit_record r ON r.record_id = d.record_id
            WHERE sl.entity_type = 'concept_relation' AND sl.entity_id = ANY(:ids) ORDER BY sl.entity_id, s.span_id""",
            {'ids': [r['relation_id'] for r in relations]}), 'entity_id')
        for r in relations:
            r['spans'] = rel_spans.get(r['relation_id'], [])
        recs = (await cls.search_recommendations(db, {'concept_id': concept_id, 'page_num': 1, 'page_size': 100}))['rows']
        findings = (await cls.search_findings(db, {'concept_id': concept_id, 'page_num': 1, 'page_size': 200}))['rows']
        assertions = await build_assertions(db, await _all(db, f"""{EDGE_SELECT_SQL}
            WHERE e.origin = 'assertion' AND (e.subject_id = :id OR e.object_id = :id) ORDER BY e.edge_id""", p))
        # 以该概念为人群条件的人群：profile 里 populations[*].conditions[*].concept_id
        populations = []
        for row in await _all(db, """
                SELECT v.profile, r.record_id, r.title AS record_title FROM kb_document_version v
                JOIN kb_document d ON d.document_id = v.document_id JOIN lit_record r ON r.record_id = d.record_id
                WHERE v.is_current AND jsonb_path_exists(v.profile, '$.populations[*].conditions[*] ? (@.concept_id == $id)',
                                                         jsonb_build_object('id', CAST(:id AS bigint)))
                ORDER BY r.record_id""", p):
            for pop in _j(row['profile']).get('populations') or []:
                for cond in pop.get('conditions') or []:
                    if cond.get('concept_id') == concept_id:
                        item = {'kind': cond.get('kind'), 'value_as_reported': cond.get('value_as_reported'), 'population_id': pop['population_id'],
                                'population_label': pop.get('label'), 'record_id': row['record_id'], 'record_title': row['record_title']}
                        if item not in populations:
                            populations.append(item)
        # 题录层只能做字面匹配：用中英文名和别名去碰标题，给出一个"文献中提及"的近似数
        names = [n for n in {c['name_zh'], c['name_en'], *[a['alias'] for a in c['aliases']]} if n and len(n) >= 3][:12]
        mention = 0
        if names:
            conds = ' OR '.join(f'title ILIKE :n{i}' for i in range(len(names)))
            mention = (await db.execute(text(f"SELECT count(*) FROM lit_record WHERE del_flag = '0' AND ({conds})"),
                                        {f'n{i}': _like(n) for i, n in enumerate(names)})).scalar()
        records = await _all(db, """
            SELECT DISTINCT r.record_id, r.title, r.pub_year, sd.design_label FROM lit_record r
            JOIN kb_document d ON d.record_id = r.record_id
            LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
            WHERE r.record_id = ANY(:rids) ORDER BY r.pub_year DESC NULLS LAST""",
            {'rids': list({x['record_id'] for x in findings + assertions + populations if x.get('record_id')} | {x['record_id'] for x in recs if x.get('record_id')})})
        return {'concept': c, 'relations': relations, 'recommendations': recs, 'findings': findings, 'assertions': assertions,
                'populations': populations, 'records': records, 'mention_count': mention, 'mention_terms': names}

    # ======================= 循证问题 =======================
    @classmethod
    async def question_bundle(cls, db: AsyncSession, code: str) -> dict | None:
        q = await _one(db, 'SELECT eq_id, code, label, status FROM kb_evidence_question WHERE code = :c', {'c': code})
        if not q:
            return None
        p = {'eq': q['eq_id']}
        joined = """FROM kb_record_question x JOIN lit_record r ON r.record_id = x.record_id
            LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current WHERE x.eq_id = :eq AND r.del_flag = '0'"""
        q['records'] = (await db.execute(text(f'SELECT count(*) {joined}'), p)).scalar()
        by_design = await _all(db, f'SELECT COALESCE(sd.design_label, \'未分类\') AS key, count(*) AS n {joined} GROUP BY 1 ORDER BY 2 DESC', p)
        by_year = await _all(db, f'SELECT r.pub_year AS key, count(*) AS n {joined} AND r.pub_year >= 1990 GROUP BY 1 ORDER BY 1', p)
        # 证据地图：研究设计 × 年代
        matrix = await _all(db, f"""SELECT COALESCE(sd.design_label, '未分类') AS design, (r.pub_year / 5 * 5) AS period, count(*) AS n
            {joined} AND r.pub_year >= 1990 GROUP BY 1, 2""", p)
        structured = await _all(db, """
            SELECT r.record_id, r.title, r.pub_year, sd.design_label, d.doc_kind,
                   (SELECT count(DISTINCT e.group_uid) FROM kb_edge e JOIN kb_document_version v ON v.version_id = e.version_id
                     WHERE v.document_id = d.document_id AND e.origin = 'finding') AS findings,
                   (SELECT count(*) FROM kb_recommendation rc JOIN kb_document_version v ON v.version_id = rc.version_id WHERE v.document_id = d.document_id) AS recs
            FROM kb_record_question x JOIN kb_document d ON d.record_id = x.record_id JOIN lit_record r ON r.record_id = x.record_id
            LEFT JOIN lit_study_design sd ON sd.record_id = r.record_id AND sd.is_current
            WHERE x.eq_id = :eq ORDER BY r.pub_year DESC NULLS LAST""", p)
        findings = (await cls.search_findings(db, {'eq': code, 'page_num': 1, 'page_size': 300}))['rows']
        return {'question': q, 'by_design': by_design, 'by_year': by_year, 'matrix': matrix, 'structured': structured, 'findings': findings}

    # ======================= 跨指南对比 =======================
    @classmethod
    async def compare(cls, db: AsyncSession, q: str | None, concept_id: int | None) -> dict:
        guidelines = await _all(db, """
            SELECT g.guideline_id, g.issuing_body, g.region, g.pub_date, r.record_id, r.title, r.pub_year,
                   (SELECT count(*) FROM kb_recommendation rc WHERE rc.guideline_id = g.guideline_id) AS recs
            FROM kb_guideline g LEFT JOIN kb_guideline_document gd ON gd.guideline_id = g.guideline_id
            LEFT JOIN kb_document d ON d.document_id = gd.document_id LEFT JOIN lit_record r ON r.record_id = d.record_id
            ORDER BY g.guideline_id""")
        recs = (await cls.search_recommendations(db, {'q': q, 'concept_id': concept_id, 'page_num': 1, 'page_size': 500}))['rows']
        matrix = await _all(db, f"""
            SELECT e.qualifiers->>'action_type' AS action_type, rc.guideline_id, {STRENGTH_SQL} AS strength, count(DISTINCT rc.rec_id) AS n
            FROM kb_recommendation rc JOIN kb_edge e ON e.group_uid = rc.uid AND e.qualifiers ? 'action_type'
            WHERE rc.rec_id = ANY(:ids) GROUP BY 1, 2, 3""", {'ids': [r['rec_id'] for r in recs]})
        return {'guidelines': guidelines, 'recommendations': recs, 'matrix': matrix}
