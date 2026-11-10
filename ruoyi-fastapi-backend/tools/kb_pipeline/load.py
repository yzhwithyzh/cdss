"""
抽取结果入库：kb_extraction_run.raw_output →
- 通用关系表 kb_edge：每个研究数值 / 研究区关系 / 推荐措施一行（origin=finding/assertion/recommendation，
  同一研究结果或同一推荐的各行 group_uid 相同）；
- 文献描述 kb_document_version.profile：本文的人群、组别、结局（只供页面展示，检索查 kb_edge）；
- 文献级元数据：研究 kb_study、指南 kb_guideline、推荐条款原文 kb_recommendation（+ 引用 kb_rec_citation）、
  术语定义 kb_term_definition、原文片段 kb_span。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.load --env dev --ids-file ids.txt [--prompt-version extract-v0.8]
  不指定 --prompt-version 时，每篇取最近一次成功的抽取。
- 每篇文献一个事务；同一文献版本重复入库时先删除上次入库的数据，再重建（幂等）。
- 概念：先按同义词归一化写法对应到已有概念，对应不上才建候选概念（status=candidate）；各写法存 kb_concept_alias。
  jsonb（qualifiers、profile）里的概念一律写在键 concept_id 下，概念合并与清理时据此改写/计数。
"""

import argparse
import hashlib
import json
import re
import uuid
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

from tools.kb_pipeline import citations, relation_spans, terms
from tools.kb_pipeline.ingest import db_dsn
from tools.kb_pipeline.prompts import DICTIONARIES
from tools.kb_pipeline.validators import _numbers, document_group_sizes, header_counts, normalize

RULE_FIELD_TYPES = {
    'adenoma_count': 'integer',
    'largest_size_mm': 'numeric',
    'histology_flags': 'concept_set',
    'ssl_count': 'integer',
    'ssl_max_size_mm': 'numeric',
    'ssl_dysplasia': 'boolean',
    'piecemeal_ge20mm': 'boolean',
    'bowel_prep_adequate': 'boolean',
    'cecal_intubation': 'boolean',
    'family_history': 'enum',
    'age': 'numeric',
}
DEFINITION_CONCEPTS = {
    'advanced adenoma': ('OUT:ADVANCED_ADENOMA', '进展期腺瘤'),
    'advanced neoplasia': ('OUT:ADVANCED_NEOPLASIA', '进展期肿瘤'),
}


def seed_dictionaries(cur: psycopg2.extensions.cursor, topic_id: int) -> None:
    """
    定义变体与患者级规则字段字典（与提示词对照表一致）；已存在则跳过
    """
    for name, (code, zh) in DEFINITION_CONCEPTS.items():
        cur.execute(
            """INSERT INTO kb_concept(topic_id, concept_type, code, name_zh, name_en, status)
               VALUES (%s, 'outcome_event', %s, %s, %s, 'active') ON CONFLICT (code) DO NOTHING""",
            (topic_id, code, zh, name),
        )
    for v in DICTIONARIES['definition_variants']:
        cur.execute(
            """INSERT INTO kb_definition_variant(variant_id, concept_id, label, components)
               SELECT %s, concept_id, %s, %s FROM kb_concept WHERE code = %s ON CONFLICT (variant_id) DO NOTHING""",
            (v['id'], v['definition'], Json({'text': v['definition']}), DEFINITION_CONCEPTS[v['concept']][0]),
        )
    for f in DICTIONARIES['rule_input_fields']:
        cur.execute(
            """INSERT INTO kb_rule_input_field(field_code, topic_id, label_zh, data_type, status)
               VALUES (%s, %s, %s, %s, 'draft') ON CONFLICT (field_code) DO NOTHING""",
            (f['code'], topic_id, f['desc'], RULE_FIELD_TYPES.get(f['code'], 'enum')),
        )


def bounds(q: dict | None) -> dict:
    """
    Quantity → {low, high, unit, as_reported}（eq/approx 上下界相同；lt/le 只有上界；gt/ge 只有下界）
    """
    if not q:
        return {}
    op, v = q.get('op'), q.get('value')
    lo, hi = q.get('value_low'), q.get('value_high')
    if op in ('eq', 'approx'):
        lo = hi = v
    elif op in ('lt', 'le'):
        lo, hi = None, v
    elif op in ('gt', 'ge'):
        lo, hi = v, None
    return {'low': lo, 'high': hi, 'unit': q.get('unit'), 'as_reported': q.get('as_reported'), 'op': op}


def compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, '', [], {})}


def locate(quote: str, text: str) -> tuple[int, int] | None:
    """
    原句在块原文中的位置（Unicode 码点偏移，右开）。先逐字查找；找不到时按 normalize（去空白、全半角、连字符）
    后查找并映射回原文位置；仍找不到返回 None
    """
    if not quote:
        return None
    i = text.find(quote)
    if i >= 0:
        return i, i + len(quote)
    norm_chars, index = [], []
    for pos, ch in enumerate(text):
        for c in normalize(ch):
            norm_chars.append(c)
            index.append(pos)
    target = normalize(quote)
    j = ''.join(norm_chars).find(target) if target else -1
    if j < 0:
        return None
    return index[j], index[j + len(target) - 1] + 1


_STOP_WORDS = {'and', 'of', 'the', 'for', 'in', 'on', 'study', 'trial', 'cohort'}
MIN_ACRONYM = 3


def study_keys(name: str) -> tuple[str, set[str], str]:
    """
    研究名称 → (规范化全名, 缩写集合, 实词首字母)。PLCO Cancer Screening Trial 与
    Prostate, Lung, Colorectal and Ovarian Cancer Screening Trial：缩写 PLCO 是后者首字母 PLCOCS… 的前缀
    """
    acronyms = {a.upper() for a in re.findall(r'\b[A-Z][A-Z0-9]{2,}\b', name)}
    words = [w for w in re.findall(r'[A-Za-z]+', name) if w.lower() not in _STOP_WORDS]
    return re.sub(r'\W+', ' ', name).strip().lower(), acronyms, ''.join(w[0].upper() for w in words)


def same_study(a: str, b: str) -> bool:
    (na, aa, ia), (nb, ab, ib) = study_keys(a), study_keys(b)
    if na == nb or aa & ab:
        return True
    return any(ib.startswith(x) for x in aa if len(x) >= MIN_ACRONYM) or any(
        ia.startswith(x) for x in ab if len(x) >= MIN_ACRONYM
    )


# 筛选阶段研究类型 → 抽取应得的文献类型（不一致时登记校验问题，供人工判断）
DESIGN_DOC_KIND = {
    '指南/共识': {'guideline', 'consensus_or_position'},
    '系统评价/Meta分析': {'systematic_review_or_ma', 'narrative_review'},
    '叙述性综述': {'narrative_review'},
    '病例报告/系列': {'case_report_or_series'},
    '算法/方法学研究': {'methodology_or_technical'},
}

# 研究结果类型 → kb_edge 谓词
FINDING_PREDICATE = {
    'comparative_effect': 'effect',
    'association': 'effect',
    'time_to_event': 'effect',
    'pooled_estimate': 'effect',
    'diagnostic_accuracy': 'diagnoses',
    'descriptive_frequency': 'frequency',
    'descriptive_continuous': 'frequency',
    'correlation': 'correlates',
}
# 研究区关系谓词 → kb_edge（增减类统一为 effect，方向写入 qualifiers.direction）
ASSERTION_PREDICATE = {
    'increases': ('effect', 'increase'),
    'decreases': ('effect', 'decrease'),
    'no_significant_difference': ('effect', 'no_sig'),
    'associated_with': ('effect', 'associated'),
}
CONTEXT = {'in_vitro': 'cell_line', 'not_applicable': 'not_reported'}
RATIO_MEASURES = {'rr', 'or', 'hr'}
DIFF_MEASURES = {'rd', 'md', 'smd'}


def direction(r: dict) -> str | None:
    """
    比值（RR/OR/HR）以 1、差值（RD/MD/SMD）以 0 为界，按置信区间判方向：increase / decrease / no_sig；
    没有置信区间或不是效应量时为 None
    """
    measure, lo, hi = r.get('measure'), r.get('ci_low'), r.get('ci_high')
    if lo is None or hi is None:
        return None
    null = 1 if measure in RATIO_MEASURES else 0 if measure in DIFF_MEASURES else None
    if null is None:
        return None
    lo, hi = min(lo, hi), max(lo, hi)
    return 'increase' if lo > null else 'decrease' if hi < null else 'no_sig'


class DocLoader:
    """
    单篇文献的入库上下文：局部引用（S1、P1、A1、O1…）→ 数据库 ID / 概念 ID
    """

    def __init__(self, cur: psycopg2.extensions.cursor, doc: dict, output: dict) -> None:
        self.cur = cur
        self.doc = doc  # document_id, version_id, topic_id, run_id, blocks{key: (block_id, page, text)}
        self.out = output
        self.span_ids: dict[str, int] = {}
        self.span_blocks: dict[str, int] = {}  # 局部 span_id → block_id（推荐引用解析用）
        self.studies_by_ref: dict[str, int] = {}
        self.study_info: dict[int, tuple[str, list[str]]] = {}  # study_id → (研究设计, 注册号)
        # 文献描述（profile）：局部引用 → 条目；人群条件概念另存，供 kb_edge.population_ids
        self.pops: dict[str, dict] = {}
        self.pop_concepts: dict[str, list[int]] = {}
        self.arms: dict[str, dict] = {}
        self.outcomes: dict[str, dict] = {}
        self.events: list[tuple[str, str, str]] = []  # (entity_type, uid, op)
        self.skipped: Counter = Counter()
        self.counts: Counter = Counter()

    # ---------- 通用 ----------
    def emit(self, entity_type: str, uid: str, op: str = 'upsert') -> None:
        self.events.append((entity_type, str(uid), op))

    def insert(self, table: str, row: dict, returning: str) -> tuple:
        cols = ', '.join(row)
        marks = ', '.join(['%s'] * len(row))
        self.cur.execute(f'INSERT INTO {table} ({cols}) VALUES ({marks}) RETURNING {returning}', list(row.values()))
        return self.cur.fetchone()

    def concept(self, cref: dict | None) -> int | None:
        """
        原文用词 → 概念：先按同义词归一化写法对应到已有概念（优先核心概念），对应不上才建候选概念；
        原文写法记入别名
        """
        if not cref or not (cref.get('text') or '').strip():
            return None
        text, ctype = cref['text'].strip(), cref.get('concept_type') or 'other'
        cjk = bool(re.search(r'[一-鿿]', text))
        concept_id = None
        if cref.get('concept_code'):
            self.cur.execute('SELECT concept_id FROM kb_concept WHERE code = %s', (cref['concept_code'],))
            row = self.cur.fetchone()
            concept_id = row[0] if row else None
        if concept_id is None:
            concept_id = terms.lookup(self.cur, text)
        if concept_id is None:
            norm = re.sub(r'\s+', ' ', text).lower()
            code = cref.get('concept_code') or f'CAND:{ctype[:16]}:{hashlib.sha1(norm.encode()).hexdigest()[:16]}'
            self.cur.execute(
                """INSERT INTO kb_concept(topic_id, concept_type, code, name_zh, name_en, status)
                   VALUES (%s, %s, %s, %s, %s, 'candidate') ON CONFLICT (code) DO NOTHING RETURNING concept_id, uid""",
                (self.doc['topic_id'], ctype, code, text if cjk else None, None if cjk else text),
            )
            row = self.cur.fetchone()
            if row:
                self.emit('concept', row[1])
                concept_id = row[0]
            else:
                self.cur.execute('SELECT concept_id FROM kb_concept WHERE code = %s', (code,))
                concept_id = self.cur.fetchone()[0]
        self.cur.execute(
            """INSERT INTO kb_concept_alias(concept_id, alias, lang, source, alias_norm)
               VALUES (%s, %s, %s, 'literature', %s) ON CONFLICT (concept_id, alias) DO NOTHING""",
            (concept_id, text[:255], 'zh' if cjk else 'en', terms.norm(text)),
        )
        return concept_id

    def spans_of(self, span_ids: list | None) -> list[int]:
        return list(dict.fromkeys(self.span_ids[s] for s in span_ids or [] if s in self.span_ids))

    def link(self, entity_type: str, entity_id: int, span_ids: list | None, role: str = 'source') -> None:
        for sid in self.spans_of(span_ids):
            self.cur.execute(
                'INSERT INTO kb_span_link(entity_type, entity_id, span_id, role) VALUES (%s,%s,%s,%s) '
                'ON CONFLICT DO NOTHING',
                (entity_type, entity_id, sid, role),
            )

    def edge(self, row: dict, span_ids: list | None) -> None:
        """
        写一条通用关系 kb_edge；qualifiers 去掉空值
        """
        q = compact(row.pop('qualifiers', None) or {})
        self.insert(
            'kb_edge',
            {
                'version_id': self.doc['version_id'],
                'qualifiers': Json(q),
                'span_ids': self.spans_of(span_ids),
                'context': self.doc.get('context') or 'human',
                **row,
            },
            'edge_id',
        )
        self.counts['edge'] += 1

    def population_concepts(self, ref: object) -> list[int]:
        """
        人群的条件概念，亚组带上上级人群的条件
        """
        out, seen = [], set()
        while isinstance(ref, str) and ref in self.pops and ref not in seen:
            seen.add(ref)
            out += self.pop_concepts.get(ref, [])
            ref = self.pops[ref].get('parent_population_id')
        return list(dict.fromkeys(out))

    def default_study(self) -> int | None:
        return next(iter(self.studies_by_ref.values()), None)

    def study_of(self, ref: object) -> int | None:
        return self.studies_by_ref.get(ref) if isinstance(ref, str) else None

    # ---------- 原文片段、研究 ----------
    def spans(self) -> None:
        for s in self.out.get('evidence_spans') or []:
            block = self.doc['blocks'].get(s.get('block_id'))
            if not block:
                self.skipped['span_block_missing'] += 1
                continue
            block_id, page, text = block
            quote = s.get('quote') or ''
            where = locate(quote, text)
            (span_id,) = self.insert(
                'kb_span',
                {
                    'version_id': self.doc['version_id'],
                    'block_id': block_id,
                    'page': s.get('page') or page,
                    'quote': quote,
                    'char_start': where[0] if where else None,
                    'char_end': where[1] if where else None,
                    'quote_verified': where is not None,
                },
                'span_id',
            )
            self.span_ids[s['span_id']] = span_id
            self.span_blocks[s['span_id']] = block_id
            self.counts['span'] += 1

    def studies(self) -> None:
        for s in self.out.get('studies') or []:
            study_id, uid = self.insert(
                'kb_study',
                {
                    'topic_id': self.doc['topic_id'],
                    'design_coarse': s.get('design_coarse') or 'other',
                    'design_as_reported': (s.get('design_as_reported') or '')[:255] or None,
                    'design_uncertainty': s.get('design_uncertainty'),
                    'registration_ids': s.get('registration_ids') or [],
                    'parent_study_name': s.get('parent_study_name'),
                    'countries': s.get('countries') or [],
                    'n_centers': s.get('n_centers'),
                    'period_as_reported': (s.get('period_as_reported') or '')[:255] or None,
                    'terminology_version': (s.get('terminology_version') or '')[:32] or None,
                    'data_source': s.get('data_source'),
                    'unit_of_analysis_default': s.get('unit_of_analysis_default'),
                    'parent_study_id': self.parent_study(s.get('parent_study_name')),
                },
                'study_id, uid',
            )
            self.studies_by_ref[s['study_ref']] = study_id
            self.study_info[study_id] = (s.get('design_coarse') or 'other', s.get('registration_ids') or [])
            self.cur.execute(
                "INSERT INTO kb_study_report(study_id, document_id, report_role, link_status) VALUES (%s,%s,'primary','confirmed')",
                (study_id, self.doc['document_id']),
            )
            self.link('study', study_id, s.get('span_ids'))
            self.emit('study', uid)
            self.counts['study'] += 1

    def parent_study(self, name: str | None) -> int | None:
        """
        母研究（PLCO、NPS…）：按名称归并到同一行（只有名称、没有文献报告的研究行），没有则新建，待人工确认
        """
        name = (name or '').strip()
        if not name:
            return None
        self.cur.execute(
            """SELECT s.study_id, s.study_name FROM kb_study s
               WHERE s.topic_id = %s AND s.study_name IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM kb_study_report r WHERE r.study_id = s.study_id)""",
            (self.doc['topic_id'],),
        )
        for study_id, existing in self.cur.fetchall():
            if same_study(name, existing):
                return study_id
        (study_id,) = self.insert(
            'kb_study',
            {'topic_id': self.doc['topic_id'], 'design_coarse': 'other', 'study_name': name[:255]},
            'study_id',
        )
        return study_id

    # ---------- 文献描述（profile）：人群、组别、结局 ----------
    def condition(self, ref: str, i: int, kind: str, c: dict) -> dict:
        q = bounds(c.get('quantity'))
        rule_field = c.get('rule_field_code') if c.get('rule_field_code') in RULE_FIELD_TYPES else None
        return compact(
            {
                'condition_id': f'{ref}.{i}',
                'kind': kind,
                'concept_id': self.concept(c.get('concept')),
                'text': (c.get('concept') or {}).get('text'),
                'qualifier': c.get('qualifier') or 'present',
                'value_low': q.get('low'),
                'value_high': q.get('high'),
                'value_unit': q.get('unit'),
                'value_as_reported': q.get('as_reported'),
                'rule_field_code': rule_field,
                'or_group': c.get('or_group'),
                'span_ids': self.spans_of(c.get('span_ids')),
            }
        )

    def populations(self) -> None:
        for p in self.out.get('populations') or []:
            ref = p.get('population_ref')
            if not isinstance(ref, str):
                continue
            age, n = p.get('age') or {}, p.get('n') or {}
            conds = []
            for kind, key in (
                ('inclusion', 'eligibility_conditions'),
                ('exclusion', 'exclusion_conditions'),
                ('subgroup_definition', 'subgroup_conditions'),
            ):
                conds += [self.condition(ref, len(conds) + j, kind, c) for j, c in enumerate(p.get(key) or [])]
            for cref in p.get('risk_background') or []:
                conds.append(self.condition(ref, len(conds), 'risk_background', {'concept': cref, 'qualifier': 'present'}))
            self.pops[ref] = compact(
                {
                    'population_id': ref,
                    'study_id': self.study_of(p.get('study_ref')) or self.default_study(),
                    'parent_population_id': p.get('parent_population_ref'),
                    'role': p.get('role') or 'overall',
                    'label': (p.get('label') or '')[:500] or '(未命名人群)',
                    'age_state': age.get('state') or 'not_reported',
                    'age_low': age.get('lower'),
                    'age_high': age.get('upper'),
                    'age_unit': age.get('unit') or 'year',
                    'age_basis': age.get('basis'),
                    'pediatric_included': p.get('pediatric_included') or 'unknown',
                    'indication_setting': p.get('indication_setting') or 'not_reported',
                    'n_value': n.get('value'),
                    'n_unit': n.get('unit'),
                    'conditions': conds,
                    'span_ids': self.spans_of(p.get('span_ids')),
                }
            )
            self.pop_concepts[ref] = [
                c['concept_id']
                for c in conds
                if c.get('concept_id') and c['kind'] != 'exclusion' and c.get('qualifier') != 'absent'
            ]
        for ref, pop in self.pops.items():  # 上级人群引用不存在时去掉
            if pop.get('parent_population_id') not in self.pops or pop.get('parent_population_id') == ref:
                pop.pop('parent_population_id', None)

    def arms_(self) -> None:
        for a in self.out.get('arms') or []:
            ref = a.get('arm_ref')
            if not isinstance(ref, str):
                continue
            items = []
            for j, i in enumerate(a.get('interventions') or []):
                dose, duration = bounds(i.get('dose')), bounds(i.get('duration'))
                items.append(
                    compact(
                        {
                            'arm_intervention_id': f'{ref}.{j}',
                            'concept_id': self.concept(i.get('concept')),
                            'text': (i.get('concept') or {}).get('text'),
                            'dose_low': dose.get('low'),
                            'dose_high': dose.get('high'),
                            'dose_unit': dose.get('unit'),
                            'dose_as_reported': dose.get('as_reported'),
                            'frequency_as_reported': i.get('frequency_as_reported'),
                            'route': i.get('route'),
                            'duration_low': duration.get('low'),
                            'duration_high': duration.get('high'),
                            'duration_unit': duration.get('unit'),
                            'details_as_reported': i.get('details_as_reported'),
                            'span_ids': self.spans_of(i.get('span_ids')),
                        }
                    )
                )
            self.arms[ref] = compact(
                {
                    'arm_id': ref,
                    'study_id': self.study_of(a.get('study_ref')) or self.default_study(),
                    'population_id': a.get('population_ref') if a.get('population_ref') in self.pops else None,
                    'label': (a.get('label') or '')[:500] or '(未命名组别)',
                    'role': a.get('role') or 'exposure_level',
                    'interventions': items,
                }
            )

    def outcomes_(self) -> None:
        for o in self.out.get('outcomes') or []:
            ref = o.get('outcome_ref')
            if not isinstance(ref, str):
                continue
            tp = o.get('timepoint') or {}
            tq = bounds(tp.get('quantity'))
            self.outcomes[ref] = compact(
                {
                    'outcome_id': ref,
                    'study_id': self.default_study(),
                    'concept_id': self.concept(o.get('concept')),
                    'label_as_reported': (o.get('label_as_reported') or '')[:500] or '(未命名结局)',
                    'ascertainment': o.get('ascertainment'),
                    'timepoint_state': tp.get('state') or 'not_reported',
                    'timepoint_low': tq.get('low'),
                    'timepoint_high': tq.get('high'),
                    'timepoint_unit': tq.get('unit'),
                    'timepoint_as_reported': tq.get('as_reported'),
                    'timepoint_anchor': tp.get('anchor'),
                    'primacy': o.get('primacy') or 'not_stated',
                    'outcome_scope': o.get('outcome_scope'),
                    'span_ids': self.spans_of(o.get('definition_span_ids')),
                }
            )

    def arm_concept(self, ref: object) -> int | None:
        items = (self.arms.get(ref) or {}).get('interventions') or [] if isinstance(ref, str) else []
        return next((i['concept_id'] for i in items if i.get('concept_id')), None)

    # ---------- 研究结果 → kb_edge ----------
    def findings(self) -> None:
        spans = {s.get('span_id'): s for s in self.out.get('evidence_spans') or []}
        doc_sizes = document_group_sizes({k: v[2] for k, v in self.doc['blocks'].items()})
        for f in self.out.get('findings') or []:
            study_id = self.study_of(f.get('study_ref')) or self.default_study()
            pop_ref = f.get('population_ref')
            if not study_id or pop_ref not in self.pops:
                self.skipped['finding_missing_study_or_population'] += 1
                continue
            cited = ''.join((spans.get(s) or {}).get('quote') or '' for s in f.get('source_span_ids') or [])
            context = set()
            for s in f.get('source_span_ids') or []:
                block = self.doc['blocks'].get((spans.get(s) or {}).get('block_id'))
                context |= header_counts(block[2]) if block else set()
            self.finding_edges(f, study_id, pop_ref, _numbers(cited), context, doc_sizes)
            self.counts['finding'] += 1

    def finding_edges(
        self, f: dict, study_id: int, pop_ref: str, cited: set[str], context: set[str], doc_sizes: set[str]
    ) -> None:
        """
        一个研究结果 → kb_edge：每个数值一行（各组的数值主语取该组的干预概念）；没有数值时写一行无数值的关系
        """
        ex, cmp_ = f.get('exposure') or {}, f.get('comparator') or {}
        ftype = f.get('finding_type') or 'other'
        predicate = FINDING_PREDICATE.get(ftype, 'observed')
        ex_concept = self.concept(ex.get('concept'))
        cmp_concept = self.concept(cmp_.get('concept'))
        ref_std = self.concept(f.get('reference_standard'))
        subject = ex_concept or self.arm_concept(ex.get('arm_ref'))
        comparator = ref_std if predicate == 'diagnoses' else cmp_concept or self.arm_concept(cmp_.get('arm_ref'))
        outcome = self.outcomes.get(f.get('outcome_ref')) or {}
        design, trials = self.study_info.get(study_id, ('other', []))
        if f.get('statement_role') == 'cited_result':  # 引用他人的结果：研究设计与注册号不是本文的
            design, trials = 'not_reported', []
        pop = self.pops[pop_ref]
        model, pooled = f.get('model') or {}, f.get('pooled') or {}
        level = bounds(ex.get('level'))
        group = str(uuid.uuid4())
        base = {
            'finding_type': ftype,
            'model': model.get('type'),
            'pooled_k': pooled.get('k_studies'),
            'pooled_n': pooled.get('n_participants'),
            'exposure_concept_id': ex_concept,
            'comparator_concept_id': cmp_concept,
            'exposure_text': ex.get('level_as_reported') or (ex.get('concept') or {}).get('text'),
            'exposure_level': compact(level) or None,
            'comparator_text': (cmp_.get('concept') or {}).get('text'),
            'exposure_arm': ex.get('arm_ref') if ex.get('arm_ref') in self.arms else None,
            'comparator_arm': cmp_.get('arm_ref') if cmp_.get('arm_ref') in self.arms else None,
            'outcome_ref': f.get('outcome_ref') if outcome else None,
            'outcome_text': outcome.get('label_as_reported'),
            'timepoint_text': outcome.get('timepoint_as_reported'),
            'population_ref': pop_ref,
            'population_text': pop.get('label'),
            'subgroup': pop.get('label') if pop.get('role') == 'subgroup' else None,
            'reference_standard_concept_id': ref_std,
        }
        adjustment = self.spans_of(model.get('adjusted_for_span_ids'))
        for r in f.get('results') or [{}]:
            arm_ref = r.get('arm_ref') if r.get('arm_ref') in self.arms else None
            if not arm_ref:
                row_subject = subject
            elif arm_ref == cmp_.get('arm_ref'):  # 对照组的数值，主语是对照
                row_subject = self.arm_concept(arm_ref) or comparator
            elif arm_ref == ex.get('arm_ref'):
                row_subject = self.arm_concept(arm_ref) or subject
            else:
                row_subject = self.arm_concept(arm_ref)
            big_n = r.get('denominator')
            denominator_source = None
            if big_n is not None and str(big_n) not in cited:
                denominator_source = (
                    'table_header' if str(big_n) in context else 'group_size' if str(big_n) in doc_sizes else 'not_found'
                )
            self.edge(
                {
                    'origin': 'finding',
                    'group_uid': group,
                    'subject_id': row_subject,
                    'predicate': predicate,
                    'object_id': outcome.get('concept_id'),
                    'comparator_id': None if arm_ref else comparator,
                    'population_ids': self.population_concepts(pop_ref),
                    'statement_role': f.get('statement_role') or 'other',
                    'design': design,
                    'trial_ids': trials,
                    'qualifiers': base
                    | {
                        'measure': r.get('measure'),
                        'value': r.get('value'),
                        'ci_low': r.get('ci_low'),
                        'ci_high': r.get('ci_high'),
                        'ci_level': r.get('ci_level'),
                        'p': r.get('p_as_reported'),
                        'unit': r.get('unit'),
                        'numerator': r.get('numerator'),
                        'denominator': big_n,
                        'denominator_source': denominator_source,
                        'category': r.get('category'),
                        'arm_ref': arm_ref,
                        'arm': self.arms[arm_ref]['label'] if arm_ref else None,
                        'value_text': r.get('value_as_reported'),
                        'direction': direction(r),
                        'adjustment_span_ids': adjustment,
                    },
                },
                f.get('source_span_ids'),
            )

    def assertions(self) -> None:
        for a in self.out.get('assertions') or []:
            s_concept = self.concept(a.get('subject'))
            o_concept = self.concept(a.get('object'))
            pred = a.get('predicate') or 'other'
            if pred == 'has_symptom' and s_concept and o_concept:
                # 疾病—症状是概念间关系：一种关系一行，各文献的出处挂在 kb_span_link 上
                self.cur.execute(
                    """INSERT INTO kb_concept_relation(parent_id, child_id, rel_type, source)
                       VALUES (%s, %s, 'has_symptom', 'literature')
                       ON CONFLICT (parent_id, child_id, rel_type) DO UPDATE SET rel_type = EXCLUDED.rel_type
                       RETURNING relation_id""",
                    (s_concept, o_concept),
                )
                self.link('concept_relation', self.cur.fetchone()[0], a.get('span_ids'))
                continue
            edge_pred, edge_dir = ASSERTION_PREDICATE.get(pred, (pred if pred != 'has_symptom' else 'other', None))
            self.edge(
                {
                    'origin': 'assertion',
                    'group_uid': str(uuid.uuid4()),
                    'subject_id': s_concept,
                    'predicate': edge_pred,
                    'object_id': o_concept,
                    'statement_role': a.get('statement_role') or 'other',
                    'design': 'not_reported'
                    if a.get('statement_role') == 'cited_result'
                    else self.study_info.get(self.default_study(), ('other', []))[0],
                    'context': a.get('context') or 'not_reported',
                    'qualifiers': {
                        'predicate_as_extracted': pred,
                        'direction': edge_dir,
                        'subject_text': (a.get('subject') or {}).get('text'),
                        'object_text': (a.get('object') or {}).get('text'),
                    },
                },
                a.get('span_ids'),
            )
            self.counts['assertion'] += 1

    # ---------- 定义、Meta 分析纳入研究 ----------
    def definitions(self) -> None:
        for d in self.out.get('definitions') or []:
            concept_id = self.concept(d.get('term'))
            value = d.get('definition_variant_id')
            variant, unmatched = None, value == 'unmatched'
            if isinstance(value, str) and value and value != 'unmatched':
                self.cur.execute('SELECT 1 FROM kb_definition_variant WHERE variant_id = %s', (value,))
                variant, unmatched = (value, False) if self.cur.fetchone() else (None, True)
            (def_id,) = self.insert(
                'kb_term_definition',
                {
                    'version_id': self.doc['version_id'],
                    'concept_id': concept_id,
                    'definition_variant_id': variant,
                    'definition_unmatched': unmatched,
                },
                'definition_id',
            )
            self.link('definition', def_id, d.get('span_ids'), 'definition')

    def meta_analysis(self) -> None:
        ma = self.out.get('meta_analysis') or {}
        study_id = self.default_study()
        if not ma or not study_id:
            return
        for seq, s in enumerate(ma.get('included_studies') or [], 1):
            self.cur.execute(
                """INSERT INTO kb_review_included_study(review_study_id, seq, label, year, registration_id, doi_or_pmid)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    study_id,
                    seq,
                    (s.get('label') or '')[:255],
                    s.get('year'),
                    (s.get('registration_id') or '')[:64] or None,
                    (s.get('doi_or_pmid') or '')[:128] or None,
                ),
            )

    # ---------- 指南推荐：条款原文进 kb_recommendation，适用条件与措施进 kb_edge ----------
    def recommendations(self, title: str) -> None:
        recs = self.out.get('recommendations') or []
        if not recs:
            return
        identity = re.sub(r'\s+', ' ', (title or f'document-{self.doc["document_id"]}')).strip()[:128]
        info = self.out.get('guideline_info') or {}
        date = re.fullmatch(
            r'\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*', info.get('publication_date_as_reported') or ''
        )
        guideline_id, uid = self.insert(
            'kb_guideline',
            {
                'topic_id': self.doc['topic_id'],
                'identity_key': identity,
                'issuing_body': (info.get('issuing_body') or '')[:500] or None,
                'region': (info.get('region') or '')[:64] or None,
                'version_label': (info.get('version_label_as_reported') or '')[:64] or f'doc-{self.doc["document_id"]}',
                'pub_date': '-'.join(date.groups()) if date else None,  # 只有完整日期才写，年份见题录
                'supersedes_as_reported': (info.get('supersedes_as_reported') or '')[:500] or None,
                'fulltext_source': 'in_library',
            },
            'guideline_id, uid',
        )
        self.cur.execute(
            "INSERT INTO kb_guideline_document(guideline_id, document_id, role) VALUES (%s,%s,'full_text')",
            (guideline_id, self.doc['document_id']),
        )
        self.emit('guideline', uid)
        spans = {s.get('span_id'): s.get('quote') or '' for s in self.out.get('evidence_spans') or []}
        rec_blocks: dict[int, list[int]] = {}
        llm_labels: dict[int, list[str]] = {}
        for rec in recs:
            text = ' '.join(spans.get(s, '') for s in rec.get('text_span_ids') or []).strip()
            rec_id, rec_uid = self.insert(
                'kb_recommendation',
                {
                    'guideline_id': guideline_id,
                    'version_id': self.doc['version_id'],
                    'number_as_reported': (rec.get('number_as_reported') or '')[:32] or None,
                    'text_verbatim': text or '(无原文)',
                    'direction_wording': (rec.get('direction_wording_as_reported') or '-')[:255],
                    'strength_as_reported': (rec.get('strength_as_reported') or '')[:64] or None,
                    'certainty_as_reported': (rec.get('certainty_as_reported') or '')[:64] or None,
                    'prefill_run_id': self.doc['run_id'],
                },
                'rec_id, uid',
            )
            self.link('recommendation', rec_id, rec.get('text_span_ids'))
            self.link('recommendation', rec_id, rec.get('exception_span_ids'), 'exception')
            self.rec_edges(rec, str(rec_uid), text)
            rec_blocks[rec_id] = [self.span_blocks[s] for s in rec.get('text_span_ids') or [] if s in self.span_blocks]
            llm_labels[rec_id] = rec.get('cited_reference_labels') or []
            self.emit('recommendation', rec_uid)
            self.counts['recommendation'] += 1
        # 引用由程序从原文引用标记与参考文献列表解析；解析不到时才保留模型给出的引用标签（依据类型 unknown）
        if citations.resolve(self.cur, self.doc['version_id'], rec_blocks) == 0:
            for rec_id, labels in llm_labels.items():
                for seq, label in enumerate(labels, 1):
                    self.cur.execute(
                        'INSERT INTO kb_rec_citation(rec_id, seq, ref_label) VALUES (%s,%s,%s)',
                        (rec_id, seq, label[:500]),
                    )

    def rec_edges(self, rec: dict, rec_uid: str, text: str) -> None:
        """
        一条推荐的每个措施一行（主语=措施对象概念）；没有措施时也写一行，保留适用条件。
        适用人群概念 = 推荐人群的条件 + 推荐适用条件（不含 absent）
        """
        pop_ids = self.population_concepts(rec.get('population_ref'))
        conds = [self.condition(rec_uid[:8], i, 'rec_condition', c) for i, c in enumerate(rec.get('conditions') or [])]
        pop_ids += [c['concept_id'] for c in conds if c.get('concept_id') and c.get('qualifier') != 'absent']
        base = {
            'conditions': conds,
            'rec_number': rec.get('number_as_reported'),
            'direction_wording': rec.get('direction_wording_as_reported'),
            'strength': rec.get('strength_as_reported'),
            'certainty': rec.get('certainty_as_reported'),
            'rec_text': text[:1000],
        }
        for act in rec.get('actions') or [{}]:
            iv = bounds(act.get('interval'))
            self.edge(
                {
                    'origin': 'recommendation',
                    'group_uid': rec_uid,
                    'subject_id': self.concept(act.get('target')),
                    'predicate': 'recommended',
                    'object_id': None,
                    'population_ids': list(dict.fromkeys(pop_ids)),
                    'statement_role': 'recommendation',
                    'design': 'guideline',
                    'qualifiers': base
                    | {
                        'action_type': act.get('action_type') or ('other' if act else None),
                        'action_text': act.get('text_as_reported'),
                        'target_text': (act.get('target') or {}).get('text'),
                        'interval_low': iv.get('low'),
                        'interval_high': iv.get('high'),
                        'interval_unit': iv.get('unit'),
                        'interval_text': iv.get('as_reported'),
                        'anchor': act.get('anchor'),
                        'phase_no': act.get('phase_no'),
                    },
                },
                rec.get('text_span_ids'),
            )

    def doc_kind_check(self, doc_kind: str | None) -> None:
        """
        抽取判定的文献类型与筛选阶段研究类型不一致时登记校验问题（不自动改，交人工判断）
        """
        self.cur.execute(  # 同一文献版本的历次抽取只保留最新一条判定
            """DELETE FROM kb_validator_issue WHERE rule_code = 'doc_kind_conflict'
                 AND run_id IN (SELECT run_id FROM kb_extraction_run WHERE version_id = %s)""",
            (self.doc['version_id'],),
        )
        self.cur.execute(
            """SELECT s.design_label FROM lit_study_design s JOIN kb_document d ON d.record_id = s.record_id
               WHERE d.document_id = %s AND s.is_current LIMIT 1""",
            (self.doc['document_id'],),
        )
        row = self.cur.fetchone()
        if not row or not doc_kind:
            return
        expected = DESIGN_DOC_KIND.get(row[0], {'original_research'})
        if doc_kind not in expected:
            self.cur.execute(
                """INSERT INTO kb_validator_issue(run_id, entity_type, rule_code, severity, message)
                   VALUES (%s, 'document', 'doc_kind_conflict', 'warn', %s)""",
                (self.doc['run_id'], f'筛选阶段为“{row[0]}”，抽取判定为 {doc_kind}，需人工确认'),
            )

    def load(self, title: str) -> dict:
        d = self.out.get('document') or {}
        self.cur.execute(
            """UPDATE kb_document SET doc_kind = %s, subject_scope = %s, triage_uncertainty = %s, evidence_layer = 2,
                      record_version = record_version + 1, update_time = now() WHERE document_id = %s RETURNING uid""",
            (d.get('doc_kind'), d.get('subject_scope'), d.get('triage_uncertainty'), self.doc['document_id']),
        )
        self.emit('document', self.cur.fetchone()[0])
        scope = d.get('subject_scope') or 'not_reported'
        self.doc['context'] = CONTEXT.get(scope, scope)
        self.doc_kind_check(d.get('doc_kind'))
        self.spans()
        self.studies()
        self.populations()
        self.arms_()
        self.outcomes_()
        self.definitions()
        self.findings()
        self.meta_analysis()
        self.assertions()
        self.recommendations(title)
        profile = {
            'run_id': self.doc['run_id'],
            'populations': list(self.pops.values()),
            'arms': list(self.arms.values()),
            'outcomes': list(self.outcomes.values()),
        }
        self.cur.execute(
            'UPDATE kb_document_version SET profile = %s WHERE version_id = %s', (Json(profile), self.doc['version_id'])
        )
        return dict(self.counts) | {
            'population': len(self.pops),
            'arm': len(self.arms),
            'outcome': len(self.outcomes),
            'skipped': dict(self.skipped),
        }


def clear_previous(cur: psycopg2.extensions.cursor, document_id: int, version_id: int) -> list[tuple[str, str]]:
    """
    删除该文献上次入库的数据，返回需要从图中删除的 (实体类型, uid)
    """
    gone: list[tuple[str, str]] = []

    def collect(entity: str, sql: str, args: tuple) -> None:
        cur.execute(sql, args)
        gone.extend((entity, str(r[0])) for r in cur.fetchall())

    cur.execute('SELECT study_id FROM kb_study_report WHERE document_id = %s', (document_id,))
    studies = [r[0] for r in cur.fetchall()]
    collect('study', 'SELECT uid FROM kb_study WHERE study_id = ANY(%s::bigint[])', (studies,))
    cur.execute('SELECT guideline_id FROM kb_guideline_document WHERE document_id = %s', (document_id,))
    guidelines = [r[0] for r in cur.fetchall()]
    collect('recommendation', 'SELECT uid FROM kb_recommendation WHERE guideline_id = ANY(%s::bigint[])', (guidelines,))
    collect('guideline', 'SELECT uid FROM kb_guideline WHERE guideline_id = ANY(%s::bigint[])', (guidelines,))
    statements = [
        ('DELETE FROM kb_edge WHERE version_id = %s', (version_id,)),
        ('UPDATE kb_document_version SET profile = NULL WHERE version_id = %s', (version_id,)),
        (
            'DELETE FROM kb_span_link WHERE span_id IN (SELECT span_id FROM kb_span WHERE version_id = %s)',
            (version_id,),
        ),
        ('DELETE FROM kb_review_included_study WHERE review_study_id = ANY(%s::bigint[])', (studies,)),
        ('DELETE FROM kb_study_report WHERE study_id = ANY(%s::bigint[])', (studies,)),
        ('DELETE FROM kb_study WHERE study_id = ANY(%s::bigint[])', (studies,)),
        (
            'DELETE FROM kb_rec_citation WHERE rec_id IN (SELECT rec_id FROM kb_recommendation WHERE guideline_id = ANY(%s::bigint[]))',
            (guidelines,),
        ),
        ('DELETE FROM kb_recommendation WHERE guideline_id = ANY(%s::bigint[])', (guidelines,)),
        ('DELETE FROM kb_guideline_document WHERE guideline_id = ANY(%s::bigint[])', (guidelines,)),
        ('DELETE FROM kb_guideline WHERE guideline_id = ANY(%s::bigint[])', (guidelines,)),
        ('DELETE FROM kb_term_definition WHERE version_id = %s', (version_id,)),
        ('DELETE FROM kb_span WHERE version_id = %s', (version_id,)),
    ]
    for sql, args in statements:
        cur.execute(sql, args)
    return gone


def write_outbox(cur: psycopg2.extensions.cursor, events: list[tuple[str, str, str]]) -> None:
    for entity_type, uid, op in events:
        cur.execute(
            """INSERT INTO kb_graph_outbox(entity_type, entity_uid, record_version, op, payload)
               VALUES (%s, %s, coalesce((SELECT max(record_version) FROM kb_graph_outbox WHERE entity_uid = %s), 0) + 1,
                       %s, '{}'::jsonb)""",
            (entity_type, uid, uid, op),
        )


def load_one(cur: psycopg2.extensions.cursor, source_id: str, prompt_version: str | None) -> dict | None:
    """
    :param prompt_version: 指定提示词版本；None 时取该文献最近一次成功的抽取
    """
    cur.execute(
        """SELECT d.document_id, d.topic_id, v.version_id, e.run_id, e.raw_output, r.title
           FROM lit_record r JOIN kb_document d ON d.record_id = r.record_id
           JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current
           JOIN kb_extraction_run e ON e.version_id = v.version_id
           WHERE r.source_id = %s AND e.status = 'to_review' AND (%s IS NULL OR e.prompt_hash = %s)
             AND e.raw_output IS NOT NULL
           ORDER BY e.finish_time DESC NULLS LAST LIMIT 1""",
        (source_id, prompt_version, prompt_version and hashlib.sha256(prompt_version.encode()).hexdigest()),
    )
    row = cur.fetchone()
    if not row:
        return None
    document_id, topic_id, version_id, run_id, output, title = row
    cur.execute('SELECT block_key, block_id, page, text FROM kb_block WHERE version_id = %s', (version_id,))
    blocks = {k: (bid, page, text) for k, bid, page, text in cur.fetchall()}
    seed_dictionaries(cur, topic_id)
    gone = clear_previous(cur, document_id, version_id)
    loader = DocLoader(
        cur,
        {
            'document_id': document_id,
            'topic_id': topic_id,
            'version_id': version_id,
            'run_id': run_id,
            'blocks': blocks,
        },
        output,
    )
    stats = loader.load(title)
    write_outbox(cur, [(t, u, 'delete') for t, u in gone] + loader.events)
    return stats | {'events': len(loader.events), 'deleted': len(gone)}


CONCEPT_REFERENCES = [
    ('kb_term_definition', 'concept_id'),
    ('kb_definition_variant', 'concept_id'),
    ('kb_concept_relation', 'parent_id'),
    ('kb_concept_relation', 'child_id'),
    ('kb_concept_code_map', 'concept_id'),
    ('kb_edge', 'subject_id'),
    ('kb_edge', 'object_id'),
    ('kb_edge', 'comparator_id'),
]
CONCEPT_ARRAY_REFERENCES = [('kb_edge', 'population_ids')]
# jsonb 中键名为 concept_id / *_concept_id 的值也是概念引用
CONCEPT_JSONB_REFERENCES = [('kb_edge', 'qualifiers'), ('kb_document_version', 'profile')]
JSONB_CONCEPT_PATH = 'lax $.**.concept_id'


def jsonb_concept_ids(cur: psycopg2.extensions.cursor) -> set[int]:
    """
    jsonb（qualifiers、profile）中引用的全部概念：键 concept_id，以及 exposure_concept_id 等 *_concept_id
    """
    ids: set[int] = set()
    for table, col in CONCEPT_JSONB_REFERENCES:
        cur.execute(
            f"""SELECT DISTINCT (kv.value #>> '{{}}')::bigint FROM {table} t,
                    LATERAL jsonb_path_query(t.{col}, 'lax $.**') obj, LATERAL jsonb_each(CASE jsonb_typeof(obj)
                        WHEN 'object' THEN obj ELSE '{{}}'::jsonb END) kv
                WHERE t.{col} IS NOT NULL AND (kv.key = 'concept_id' OR kv.key LIKE '%%\\_concept\\_id')
                  AND jsonb_typeof(kv.value) = 'number'"""
        )
        ids |= {r[0] for r in cur.fetchall()}
    return ids


def prune_orphan_concepts(cur: psycopg2.extensions.cursor) -> int:
    """
    删除不再被任何数据引用的候选概念（重新入库后遗留），并发出图删除事件；人工确认过的概念（非 candidate）不删
    """
    # 文献抽取、未审核且已没有任何出处的概念关系先删（重新入库后出处被清空）
    cur.execute(
        """DELETE FROM kb_concept_relation r
           WHERE r.source = 'literature' AND r.review_status = 'pending'
             AND NOT EXISTS (SELECT 1 FROM kb_span_link l
                             WHERE l.entity_type = 'concept_relation' AND l.entity_id = r.relation_id)"""
    )
    refs = ' AND '.join(
        [
            # 自动挂的上级（terms attach）不算“被引用”：候选概念不再被数据引用时连同它一起删
            f'NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{col} = c.concept_id'
            + (" AND t.source <> 'auto_parent')" if table == 'kb_concept_relation' else ')')
            for table, col in CONCEPT_REFERENCES
        ]
        + [
            f'NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{col} @> ARRAY[c.concept_id])'
            for table, col in CONCEPT_ARRAY_REFERENCES
        ]
    )
    cur.execute(f"SELECT c.concept_id, c.uid::text FROM kb_concept c WHERE c.status = 'candidate' AND {refs}")
    in_jsonb = jsonb_concept_ids(cur)
    rows = [r for r in cur.fetchall() if r[0] not in in_jsonb]
    if rows:
        ids = [r[0] for r in rows]
        cur.execute(
            "DELETE FROM kb_concept_relation WHERE source = 'auto_parent' AND child_id = ANY(%s::bigint[])", (ids,)
        )
        cur.execute('DELETE FROM kb_concept_alias WHERE concept_id = ANY(%s::bigint[])', (ids,))
        cur.execute('DELETE FROM kb_concept WHERE concept_id = ANY(%s::bigint[])', (ids,))
        write_outbox(cur, [('concept', uid, 'delete') for _, uid in rows])
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description='抽取结果入库')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--ids', default='')
    ap.add_argument('--ids-file', default='')
    ap.add_argument('--prompt-version', default=None)
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(',') if x.strip()]
    if a.ids_file:
        ids += Path(a.ids_file).read_text(encoding='utf-8').split()
    conn = psycopg2.connect(db_dsn(a.env))
    total: Counter = Counter()
    try:
        for sid in ids:
            try:
                with conn, conn.cursor() as cur:  # 每篇一个事务，失败只回滚该篇
                    stats = load_one(cur, sid, a.prompt_version)
            except psycopg2.Error as e:
                print(f'{sid:>6} 失败 {type(e).__name__}: {str(e).splitlines()[0][:200]}')
                continue
            if stats is None:
                print(f'{sid:>6} 无可用抽取结果（{a.prompt_version}）')
                continue
            total.update({k: v for k, v in stats.items() if isinstance(v, int)})
            print(f'{sid:>6} {json.dumps(stats, ensure_ascii=False)}')
        with conn, conn.cursor() as cur:
            # 审核拆分出的概念关系（source=review）按备注原句重新挂原文片段：重载会删掉本文旧片段
            relinked = relation_spans.repair(cur)
            pruned = prune_orphan_concepts(cur)
            # 不再被任何研究引用的母研究行（只有名称、没有文献报告）
            cur.execute(
                """DELETE FROM kb_study p WHERE p.study_name IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM kb_study_report r WHERE r.study_id = p.study_id)
                     AND NOT EXISTS (SELECT 1 FROM kb_study c WHERE c.parent_study_id = p.study_id)"""
            )
    finally:
        conn.close()
    print('合计', dict(total), f'；清理无引用的候选概念 {pruned} 个；审核关系补原文链接 {len(relinked["linked"])} 条，'
          f'仍缺 {len(relinked["missing"])} 条')


if __name__ == '__main__':
    main()
