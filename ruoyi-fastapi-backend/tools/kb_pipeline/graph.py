"""
【已失效 2026-11-10】本工具读取 v0.8 已删除的 PICO 表（kb_finding、kb_population、kb_arm…），
事实已改存 kb_edge；Neo4j 副本未部署，需要时按 kb_edge 重写后再用。

PG → Neo4j 图同步（设计见 kb_schema/neo4j_model.md）

    python -m tools.kb_pipeline.graph init      # 建约束与索引（可重复执行）
    python -m tools.kb_pipeline.graph sync      # 消费 kb_graph_outbox 中未处理的事件
    python -m tools.kb_pipeline.graph rebuild   # 清空图，按 PG 现状全量重建，并把积压事件标记为已处理
    python -m tools.kb_pipeline.graph stats     # 图中各类节点/关系数量

规则：
- 节点身份 = PG uid；事件只带 (实体类型, uid)，节点属性与出边按 PG 当前数据现查生成（PG 为权威，图可重建）；
- 实体在 PG 中已不存在 → 删除节点；
- 出边归源节点所有：每次更新先删该节点全部出边再按当前数据重建；目标节点未同步时建占位节点（stub=true）。
"""

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import dotenv_values

from tools.kb_pipeline.ingest import db_dsn
from tools.pdf_parsing.config import BACKEND_ROOT

Cursor = psycopg2.extensions.cursor
Built = tuple[dict, list] | None
CONSTRAINTS = Path(__file__).with_name('schema') / 'neo4j_constraints.cypher'
LABELS = {
    'document': 'Document',
    'concept': 'Concept',
    'study': 'Study',
    'population': 'Population',
    'arm': 'Arm',
    'outcome': 'Outcome',
    'finding': 'Finding',
    'result': 'Result',
    'effect_modification': 'EffectModification',
    'assertion': 'Assertion',
    'guideline': 'Guideline',
    'recommendation': 'Recommendation',
}
UPSERT = """
MERGE (n:$($label) {uid: $uid})
SET n += $props, n.stub = false
WITH n
OPTIONAL MATCH (n)-[old]->()
DELETE old
WITH DISTINCT n
UNWIND $edges AS e
MERGE (m:$(e.target_label) {uid: e.target_uid})
  ON CREATE SET m.stub = true
CREATE (n)-[r:$(e.type)]->(m)
SET r += e.props
RETURN count(r) AS edges
"""
UPSERT_NO_EDGES = """
MERGE (n:$($label) {uid: $uid})
SET n += $props, n.stub = false
WITH n
OPTIONAL MATCH (n)-[old]->()
DELETE old
RETURN 0 AS edges
"""


class Neo4j:
    """
    Neo4j HTTP Query API v2 最小客户端（无需驱动）
    """

    def __init__(self, env_name: str) -> None:
        env: dict = {}
        for name in (f'.env.{env_name}', f'.env.{env_name}.local'):
            if (BACKEND_ROOT / name).is_file():
                env.update(dotenv_values(BACKEND_ROOT / name))
        env.update(os.environ)

        def get(key: str, default: str = '') -> str:
            return (env.get(key) or default).strip().strip('\'"')

        self.url = get('LIT_NEO4J_HTTP', 'http://127.0.0.1:7474/db/neo4j/query/v2')
        password = get('LIT_NEO4J_PASSWORD')
        if not password:
            raise RuntimeError('未配置 LIT_NEO4J_PASSWORD（请写入 .env.dev.local）')
        self.auth = base64.b64encode(f'{get("LIT_NEO4J_USER", "neo4j")}:{password}'.encode()).decode()

    def run(self, statement: str, **params: object) -> dict:
        body = json.dumps({'statement': statement, 'parameters': params}, default=_jsonable).encode('utf-8')
        req = urllib.request.Request(
            self.url,
            data=body,
            method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': 'Basic ' + self.auth},
        )
        try:
            out = json.loads(urllib.request.urlopen(req, timeout=60).read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            raise RuntimeError(e.read().decode('utf-8')[:500]) from None
        if out.get('errors'):
            raise RuntimeError(out['errors'])
        return out['data']

    def run_file(self, path: Path) -> int:
        text = '\n'.join(
            line for line in path.read_text(encoding='utf-8').splitlines() if not line.strip().startswith('//')
        )
        statements = [s.strip() for s in text.split(';') if s.strip()]
        for s in statements:
            self.run(s)
        return len(statements)


def _jsonable(v: object) -> object:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


def _clean(props: dict) -> dict:
    """
    Neo4j 属性不接受 None 与嵌套对象：去掉 None，Decimal 转 float，列表保留标量
    """
    out = {}
    for k, v in props.items():
        if v is None or v == []:
            continue
        out[k] = float(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, (date, datetime)) else v
    return out


def _edge(etype: str, target_label: str, target_uid: object, **props: object) -> dict | None:
    if target_uid is None:
        return None
    return {'type': etype, 'target_label': target_label, 'target_uid': str(target_uid), 'props': _clean(props)}


def _rows(cur: psycopg2.extensions.cursor, sql: str, args: tuple) -> list[tuple]:
    cur.execute(sql, args)
    return cur.fetchall()


# ---------------- 各实体：PG → (属性, 出边) ----------------
def build(cur: psycopg2.extensions.cursor, entity: str, uid: str) -> tuple[dict, list[dict]] | None:
    builder = {
        'document': _document,
        'concept': _concept,
        'study': _study,
        'population': _population,
        'arm': _arm,
        'outcome': _outcome,
        'finding': _finding,
        'result': _result,
        'effect_modification': _em,
        'assertion': _assertion,
        'guideline': _guideline,
        'recommendation': _recommendation,
    }.get(entity)
    if builder is None:
        return None
    built = builder(cur, uid)
    if built is None:
        return None
    props, edges = built
    return _clean(props), [e for e in edges if e]


def _document(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT d.record_id, l.source_id, l.title, l.pub_year, d.doc_kind, d.subject_scope, d.evidence_layer,
                             d.retraction_status, d.record_version
                      FROM kb_document d LEFT JOIN lit_record l USING (record_id) WHERE d.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    rid, sid, title, year, kind, scope, layer, retr, rv = r[0]
    return {
        'record_id': rid,
        'source_id': sid,
        'title': title,
        'year': year,
        'doc_kind': kind,
        'subject_scope': scope,
        'evidence_layer': layer,
        'retraction_status': retr,
        'record_version': rv,
    }, []


def _concept(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT c.code, c.name_zh, c.name_en, c.concept_type, c.status, c.record_version,
                             string_agg(a.alias, ' | ')
                      FROM kb_concept c LEFT JOIN kb_concept_alias a USING (concept_id) WHERE c.uid = %s
                      GROUP BY 1, 2, 3, 4, 5, 6""",
        (uid,),
    )
    if not r:
        return None
    code, zh, en, ctype, status, rv, aliases = r[0]
    edges = [
        _edge(rt, 'Concept', p_uid)
        for rt, p_uid in _rows(
            cur,
            """SELECT upper(rel.rel_type), p.uid FROM kb_concept_relation rel JOIN kb_concept c ON c.concept_id = rel.child_id
                JOIN kb_concept p ON p.concept_id = rel.parent_id WHERE c.uid = %s""",
            (uid,),
        )
    ]
    return {
        'code': code,
        'name_zh': zh,
        'name_en': en,
        'concept_type': ctype,
        'status': status,
        'aliases_text': aliases,
        'record_version': rv,
    }, edges


def _study(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT s.study_id, s.design_coarse, s.design_as_reported, s.design_uncertainty, s.countries,
                             s.registration_ids, s.parent_study_name, s.unit_of_analysis_default, s.review_status,
                             s.record_version, p.uid
                      FROM kb_study s LEFT JOIN kb_study p ON p.study_id = s.parent_study_id WHERE s.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    sid, design, design_rep, unc, countries, regs, parent_name, unit, status, rv, parent_uid = r[0]
    edges = [
        _edge('REPORTED_IN', 'Document', d_uid, report_role=role)
        for d_uid, role in _rows(
            cur,
            """SELECT d.uid, sr.report_role FROM kb_study_report sr JOIN kb_document d USING (document_id)
                WHERE sr.study_id = %s""",
            (sid,),
        )
    ]
    edges.append(_edge('PART_OF_STUDY', 'Study', parent_uid))
    edges += [
        _edge('INCLUDES', 'Study', m_uid, match_status=st)
        for m_uid, st in _rows(
            cur,
            """SELECT m.uid, ri.match_status FROM kb_review_included_study ri JOIN kb_study m ON m.study_id = ri.matched_study_id
                WHERE ri.review_study_id = %s""",
            (sid,),
        )
    ]
    return {
        'design_coarse': design,
        'design_as_reported': design_rep,
        'design_uncertainty': unc,
        'countries': list(countries or []),
        'registration_ids': list(regs or []),
        'parent_study_name': parent_name,
        'unit_of_analysis_default': unit,
        'review_status': status,
        'record_version': rv,
    }, edges


def _population(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT p.population_id, p.role, p.label, p.age_state, lower(p.age_range), upper(p.age_range),
                             lower_inc(p.age_range), upper_inc(p.age_range), p.age_basis, p.indication_setting,
                             p.pediatric_included, p.n_value, p.n_unit, p.review_status, p.record_version, s.uid, pp.uid
                      FROM kb_population p LEFT JOIN kb_study s USING (study_id)
                      LEFT JOIN kb_population pp ON pp.population_id = p.parent_population_id WHERE p.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    (
        pid,
        role,
        label,
        age_state,
        age_lo,
        age_hi,
        lo_inc,
        hi_inc,
        basis,
        setting,
        ped,
        n,
        n_unit,
        status,
        rv,
        s_uid,
        parent_uid,
    ) = r[0]
    edges = [_edge('OF_STUDY', 'Study', s_uid), _edge('SUBGROUP_OF', 'Population', parent_uid)]
    for c_uid, kind, qual, lo, hi, li, hi_i, unit, rep, field, row_id in _rows(
        cur,
        """SELECT c.uid, pc.kind, pc.qualifier, lower(pc.value_range), upper(pc.value_range), lower_inc(pc.value_range),
                       upper_inc(pc.value_range), pc.value_unit, pc.value_as_reported, pc.rule_field_code,
                       pc.condition_id
                FROM kb_population_condition pc JOIN kb_concept c USING (concept_id) WHERE pc.population_id = %s""",
        (pid,),
    ):
        edges.append(
            _edge(
                'HAS_CONDITION',
                'Concept',
                c_uid,
                kind=kind,
                qualifier=qual,
                value_low=lo,
                value_high=hi,
                low_inclusive=li,
                high_inclusive=hi_i,
                unit=unit,
                value_as_reported=rep,
                rule_field=field,
                source_table='kb_population_condition',
                source_id=row_id,
            )
        )
    return {
        'role': role,
        'label': label,
        'age_state': age_state,
        'age_lower': age_lo,
        'age_upper': age_hi,
        'age_lower_inclusive': lo_inc if age_lo is not None else None,
        'age_upper_inclusive': hi_inc if age_hi is not None else None,
        'age_basis': basis,
        'indication_setting': setting,
        'pediatric_included': ped,
        'n': n,
        'n_unit': n_unit,
        'review_status': status,
        'record_version': rv,
    }, edges


def _arm(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT a.arm_id, a.label, a.role, s.uid, p.uid FROM kb_arm a JOIN kb_study s USING (study_id)
                      LEFT JOIN kb_population p ON p.population_id = a.population_id WHERE a.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    aid, label, role, s_uid, p_uid = r[0]
    edges = [_edge('OF_STUDY', 'Study', s_uid), _edge('IN_POPULATION', 'Population', p_uid)]
    for c_uid, dose, dose_unit, freq, details, row_id in _rows(
        cur,
        """SELECT c.uid, ai.dose_as_reported, ai.dose_unit, ai.frequency_as_reported, ai.details_as_reported,
                       ai.arm_intervention_id
                FROM kb_arm_intervention ai JOIN kb_concept c USING (concept_id) WHERE ai.arm_id = %s""",
        (aid,),
    ):
        edges.append(
            _edge(
                'RECEIVES',
                'Concept',
                c_uid,
                dose=dose,
                dose_unit=dose_unit,
                frequency=freq,
                details=details,
                source_table='kb_arm_intervention',
                source_id=row_id,
            )
        )
    return {'label': label, 'role': role}, edges


def _outcome(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT o.label_as_reported, o.primacy, o.ascertainment, o.outcome_scope, o.timepoint_state,
                             lower(o.timepoint_range), upper(o.timepoint_range), o.timepoint_unit, o.timepoint_anchor,
                             o.definition_variant_id, o.definition_unmatched, s.uid, c.uid
                      FROM kb_outcome o JOIN kb_study s USING (study_id) LEFT JOIN kb_concept c USING (concept_id)
                      WHERE o.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    label, prim, asc, scope, tp_state, tp_lo, tp_hi, tp_unit, anchor, variant, unmatched, s_uid, c_uid = r[0]
    edges = [
        _edge('OF_STUDY', 'Study', s_uid),
        _edge('OF_CONCEPT', 'Concept', c_uid),
        _edge('USES_DEFINITION', 'DefinitionVariant', variant),
    ]
    return {
        'label_as_reported': label,
        'primacy': prim,
        'ascertainment': asc,
        'outcome_scope': scope,
        'timepoint_state': tp_state,
        'timepoint_low': tp_lo,
        'timepoint_high': tp_hi,
        'timepoint_unit': tp_unit,
        'timepoint_anchor': anchor,
        'definition_unmatched': unmatched,
    }, edges


def _arm_concepts(cur: Cursor, arm_id: int) -> list[str]:
    return [
        u
        for (u,) in _rows(
            cur,
            """SELECT DISTINCT c.uid FROM kb_arm_intervention ai JOIN kb_concept c USING (concept_id)
                                       WHERE ai.arm_id = %s""",
            (arm_id,),
        )
    ]


def _finding(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT f.finding_id, f.statement_role, f.finding_type, f.unit_of_analysis, f.model_type,
                             f.hypothesis_type, f.review_status, f.review_tier, f.exposure_as_reported, f.record_version,
                             s.uid, d.uid, p.uid, ec.uid, f.exposure_arm_id, ea.uid, cc.uid, f.comparator_arm_id, ca.uid,
                             o.uid, rs.uid, pf.uid
                      FROM kb_finding f JOIN kb_study s USING (study_id)
                      JOIN kb_document_version v ON v.version_id = f.version_id JOIN kb_document d ON d.document_id = v.document_id
                      JOIN kb_population p ON p.population_id = f.population_id
                      LEFT JOIN kb_concept ec ON ec.concept_id = f.exposure_concept_id
                      LEFT JOIN kb_arm ea ON ea.arm_id = f.exposure_arm_id
                      LEFT JOIN kb_concept cc ON cc.concept_id = f.comparator_concept_id
                      LEFT JOIN kb_arm ca ON ca.arm_id = f.comparator_arm_id
                      LEFT JOIN kb_outcome o ON o.outcome_id = f.outcome_id
                      LEFT JOIN kb_concept rs ON rs.concept_id = f.reference_standard_concept_id
                      LEFT JOIN kb_finding pf ON pf.finding_id = f.parent_finding_id
                      WHERE f.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    (
        fid,
        role,
        ftype,
        unit,
        model,
        hyp,
        status,
        tier,
        exp_rep,
        rv,
        s_uid,
        d_uid,
        p_uid,
        ec_uid,
        ea_id,
        ea_uid,
        cc_uid,
        ca_id,
        ca_uid,
        o_uid,
        rs_uid,
        pf_uid,
    ) = r[0]
    edges = [
        _edge('OF_STUDY', 'Study', s_uid),
        _edge('FROM_DOCUMENT', 'Document', d_uid),
        _edge('IN_POPULATION', 'Population', p_uid),
        _edge('EXPOSURE', 'Concept', ec_uid),
        _edge('EXPOSURE_ARM', 'Arm', ea_uid),
        _edge('COMPARATOR', 'Concept', cc_uid),
        _edge('COMPARATOR_ARM', 'Arm', ca_uid),
        _edge('OUTCOME', 'Outcome', o_uid),
        _edge('REFERENCE_STANDARD', 'Concept', rs_uid),
        _edge('SUBGROUP_OF', 'Finding', pf_uid),
    ]
    if not ec_uid and ea_id:  # 组别比较：由组别的干预概念推导暴露边，便于按概念查询
        edges += [_edge('EXPOSURE', 'Concept', u, via_arm=True) for u in _arm_concepts(cur, ea_id)]
    if not cc_uid and ca_id:
        edges += [_edge('COMPARATOR', 'Concept', u, via_arm=True) for u in _arm_concepts(cur, ca_id)]
    edges += [
        _edge('HAS_RESULT', 'Result', u)
        for (u,) in _rows(cur, 'SELECT uid FROM kb_finding_result WHERE finding_id = %s ORDER BY result_id', (fid,))
    ]
    return {
        'statement_role': role,
        'finding_type': ftype,
        'unit_of_analysis': unit,
        'model_type': model,
        'hypothesis_type': hyp,
        'review_status': status,
        'review_tier': tier,
        'exposure_as_reported': exp_rep,
        'record_version': rv,
    }, edges


def _result(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT r.measure, r.category, r.value, r.ci_low, r.ci_high, r.ci_level, r.unit, r.value_as_reported,
                             r.p_as_reported, r.numerator, r.denominator, r.derived, r.derivation, a.uid
                      FROM kb_finding_result r LEFT JOIN kb_arm a ON a.arm_id = r.arm_id WHERE r.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    measure, cat, value, lo, hi, level, unit, rep, p, n, big_n, derived, derivation, a_uid = r[0]
    return {
        'measure': measure,
        'category': cat,
        'value': value,
        'ci_low': lo,
        'ci_high': hi,
        'ci_level': level,
        'unit': unit,
        'value_as_reported': rep,
        'p_as_reported': p,
        'numerator': n,
        'denominator': big_n,
        'derived': derived,
        'denominator_source': (derivation or {}).get('denominator_source'),
    }, [_edge('FOR_ARM', 'Arm', a_uid)]


def _em(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT e.em_id, e.prespecified, e.has_interaction_test, e.interaction_p_as_reported, e.interaction_p,
                             e.interaction_measure, s.uid, c.uid, f.uid
                      FROM kb_effect_modification e JOIN kb_study s USING (study_id)
                      LEFT JOIN kb_concept c ON c.concept_id = e.modifier_concept_id
                      LEFT JOIN kb_finding f ON f.finding_id = e.overall_finding_id WHERE e.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    em_id, pre, has_test, p_rep, p, measure, s_uid, c_uid, f_uid = r[0]
    edges = [_edge('OF_STUDY', 'Study', s_uid), _edge('MODIFIER', 'Concept', c_uid), _edge('OVERALL', 'Finding', f_uid)]
    edges += [
        _edge('COMPARES', 'Finding', u)
        for (u,) in _rows(
            cur,
            """SELECT f.uid FROM kb_effect_modification_finding ef JOIN kb_finding f USING (finding_id)
                WHERE ef.em_id = %s""",
            (em_id,),
        )
    ]
    return {
        'prespecified': pre,
        'has_interaction_test': has_test,
        'interaction_p_as_reported': p_rep,
        'interaction_p': p,
        'interaction_measure': measure,
    }, edges


def _assertion(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT a.predicate, a.context, a.statement_role, a.review_status, sc.uid, oc.uid, d.uid
                      FROM kb_assertion a JOIN kb_document_version v USING (version_id) JOIN kb_document d USING (document_id)
                      LEFT JOIN kb_concept sc ON sc.concept_id = a.subject_concept_id
                      LEFT JOIN kb_concept oc ON oc.concept_id = a.object_concept_id WHERE a.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    pred, ctx, role, status, s_uid, o_uid, d_uid = r[0]
    return {'predicate': pred, 'context': ctx, 'statement_role': role, 'review_status': status}, [
        _edge('SUBJECT', 'Concept', s_uid),
        _edge('OBJECT', 'Concept', o_uid),
        _edge('FROM_DOCUMENT', 'Document', d_uid),
    ]


def _guideline(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT guideline_id, identity_key, issuing_body, region, version_label, fulltext_source, review_status
                      FROM kb_guideline WHERE uid = %s""",
        (uid,),
    )
    if not r:
        return None
    gid, identity, body, region, version, source, status = r[0]
    edges = [
        _edge('PUBLISHED_AS', 'Document', u, role=role)
        for u, role in _rows(
            cur,
            """SELECT d.uid, gd.role FROM kb_guideline_document gd JOIN kb_document d USING (document_id)
                WHERE gd.guideline_id = %s""",
            (gid,),
        )
    ]
    edges += [
        _edge('HAS_RECOMMENDATION', 'Recommendation', u)
        for (u,) in _rows(cur, 'SELECT uid FROM kb_recommendation WHERE guideline_id = %s ORDER BY rec_id', (gid,))
    ]
    return {
        'identity_key': identity,
        'issuing_body': body,
        'region': region,
        'version_label': version,
        'fulltext_source': source,
        'review_status': status,
    }, edges


def _recommendation(cur: Cursor, uid: str) -> Built:
    r = _rows(
        cur,
        """SELECT r.rec_id, r.number_as_reported, r.direction_wording, r.strength_as_reported, r.certainty_as_reported,
                  r.encodability, r.review_status, left(r.text_verbatim, 500), p.uid
           FROM kb_recommendation r LEFT JOIN kb_population p ON p.population_id = r.population_id WHERE r.uid = %s""",
        (uid,),
    )
    if not r:
        return None
    rid, number, direction, strength, certainty, enc, status, text, p_uid = r[0]
    edges = [_edge('IN_POPULATION', 'Population', p_uid)]
    for c_uid, field, qual, lo, hi, unit, rep, row_id in _rows(
        cur,
        """SELECT c.uid, rc.rule_field_code, rc.qualifier, lower(rc.value_range), upper(rc.value_range), rc.value_unit,
                       rc.value_as_reported, rc.rec_condition_id FROM kb_rec_condition rc JOIN kb_concept c USING (concept_id)
                WHERE rc.rec_id = %s""",
        (rid,),
    ):
        edges.append(
            _edge(
                'CONDITION',
                'Concept',
                c_uid,
                rule_field=field,
                qualifier=qual,
                value_low=lo,
                value_high=hi,
                unit=unit,
                value_as_reported=rep,
                source_table='kb_rec_condition',
                source_id=row_id,
            )
        )
    actions = []
    for c_uid, atype, lo, hi, unit, anchor, phase, text_act, row_id in _rows(
        cur,
        """SELECT c.uid, ra.action_type, lower(ra.interval_range), upper(ra.interval_range), ra.interval_unit, ra.anchor,
                       ra.phase_no, ra.text_as_reported, ra.rec_action_id
                FROM kb_rec_action ra LEFT JOIN kb_concept c ON c.concept_id = ra.target_concept_id WHERE ra.rec_id = %s""",
        (rid,),
    ):
        actions.append(atype)
        edges.append(
            _edge(
                'ACTION',
                'Concept',
                c_uid,
                action_type=atype,
                interval_low=lo,
                interval_high=hi,
                interval_unit=unit,
                anchor=anchor,
                phase=phase,
                text=text_act,
                source_table='kb_rec_action',
                source_id=row_id,
            )
        )
    return {
        'number': number,
        'direction_wording': direction,
        'strength': strength,
        'certainty': certainty,
        'encodability': enc,
        'review_status': status,
        'text': text,
        'action_types': actions,
    }, edges


# ---------------- 同步 ----------------
def apply(neo: Neo4j, cur: psycopg2.extensions.cursor, entity: str, uid: str, op: str) -> str:
    label = LABELS.get(entity)
    if label is None:
        return 'skipped'
    built = None if op == 'delete' else build(cur, entity, uid)
    if built is None:
        neo.run('MATCH (n:$($label) {uid: $uid}) DETACH DELETE n', label=label, uid=uid)
        return 'deleted'
    props, edges = built
    neo.run(UPSERT if edges else UPSERT_NO_EDGES, label=label, uid=uid, props=props, edges=edges)
    return 'upserted'


def sync_dictionaries(conn: psycopg2.extensions.connection, neo: Neo4j) -> int:
    """
    定义变体字典 → DefinitionVariant 节点（uid = variant_id），并连到所定义的概念
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT dv.variant_id, dv.label, dv.status, c.uid::text FROM kb_definition_variant dv
               JOIN kb_concept c USING (concept_id)"""
        )
        rows = cur.fetchall()
        for c_uid in {r[3] for r in rows}:  # 字典概念由种子数据写入、无同步事件，这里一并同步
            apply(neo, cur, 'concept', c_uid, 'upsert')
    for variant_id, label, status, c_uid in rows:
        neo.run(
            UPSERT,
            label='DefinitionVariant',
            uid=variant_id,
            props=_clean({'variant_id': variant_id, 'label': label, 'status': status}),
            edges=[_edge('DEFINES', 'Concept', c_uid)],
        )
    return len(rows)


def sync(conn: psycopg2.extensions.connection, neo: Neo4j, batch: int = 500) -> dict:
    stats: dict[str, int] = {}
    while True:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT event_id, entity_type, entity_uid::text, op FROM kb_graph_outbox
                   WHERE processed_time IS NULL ORDER BY event_id LIMIT %s""",
                (batch,),
            )
            events = cur.fetchall()
        if not events:
            stats['definition_variants'] = sync_dictionaries(conn, neo)
            return stats
        for event_id, entity, uid, op in events:
            with conn, conn.cursor() as cur:
                result = apply(neo, cur, entity, uid, op)
                cur.execute('UPDATE kb_graph_outbox SET processed_time = now() WHERE event_id = %s', (event_id,))
            stats[result] = stats.get(result, 0) + 1
        print(f'  已处理 {sum(stats.values())} 个事件 {stats}', flush=True)


ENTITY_TABLES = [
    ('concept', 'kb_concept'),
    ('document', 'kb_document'),
    ('study', 'kb_study'),
    ('population', 'kb_population'),
    ('arm', 'kb_arm'),
    ('outcome', 'kb_outcome'),
    ('result', 'kb_finding_result'),
    ('finding', 'kb_finding'),
    ('effect_modification', 'kb_effect_modification'),
    ('assertion', 'kb_assertion'),
    ('guideline', 'kb_guideline'),
    ('recommendation', 'kb_recommendation'),
]


def rebuild(conn: psycopg2.extensions.connection, neo: Neo4j) -> dict:
    neo.run('MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 2000 ROWS')
    stats: dict[str, int] = {}
    with conn, conn.cursor() as cur:
        cur.execute('UPDATE kb_graph_outbox SET processed_time = now() WHERE processed_time IS NULL')
        for entity, table in ENTITY_TABLES:
            if entity == 'document':
                cur.execute('SELECT uid::text FROM kb_document WHERE evidence_layer = 2')
            else:
                cur.execute(f'SELECT uid::text FROM {table}')
            uids = [u for (u,) in cur.fetchall()]
            for uid in uids:
                apply(neo, cur, entity, uid, 'upsert')
            stats[entity] = len(uids)
            print(f'  {entity}: {len(uids)}', flush=True)
    stats['definition_variants'] = sync_dictionaries(conn, neo)
    neo.run('MATCH (n) WHERE n.stub = true AND NOT (n)--() DELETE n')
    return stats


def graph_stats(neo: Neo4j) -> None:
    for row in neo.run('MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC')['values']:
        print(f'  节点 {row[0]:<20} {row[1]}')
    for row in neo.run('MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC')['values']:
        print(f'  关系 {row[0]:<20} {row[1]}')
    stub = neo.run('MATCH (n) WHERE n.stub = true RETURN count(n)')['values'][0][0]
    print(f'  占位节点（目标尚未同步）{stub}')


def main() -> None:
    ap = argparse.ArgumentParser(description='PG → Neo4j 图同步')
    ap.add_argument('command', choices=['init', 'sync', 'rebuild', 'stats'])
    ap.add_argument('--env', default='dev')
    a = ap.parse_args()
    neo = Neo4j(a.env)
    if a.command == 'init':
        print('执行约束/索引语句', neo.run_file(CONSTRAINTS), '条')
        return
    if a.command == 'stats':
        graph_stats(neo)
        return
    conn = psycopg2.connect(db_dsn(a.env))
    try:
        print('完成', sync(conn, neo) if a.command == 'sync' else rebuild(conn, neo))
        graph_stats(neo)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
