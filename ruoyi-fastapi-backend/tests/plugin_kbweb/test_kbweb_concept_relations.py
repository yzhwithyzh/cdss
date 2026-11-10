"""
概念关系的三个问题：被拒的关系不能出现；"表现为"方向不能反；只有关系、没有证据条目的概念不能被当成"无关联"。
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plugins.kbweb.agent.agent import build_tools, rel_phrase
from plugins.kbweb.dao.kbweb_dao import KbwebDao
from tests.plugin_kbweb.test_kbweb_dao import DSN, run

BLOOD_IN_STOOL, CRC, SRUS, COLIC_PAIN = 4052, 3967, 4723, 4860


def test_rel_phrase_reads_in_natural_direction():
    # is_a：子 属于 父；has_symptom：疾病（父）表现为 症状（子）。direction='parent' 表示 other 是父端
    assert rel_phrase('is_a', 'parent', '无蒂锯齿状病变', '锯齿状病变') == '无蒂锯齿状病变 属于 锯齿状病变'
    assert rel_phrase('is_a', 'child', '锯齿状病变', '无蒂锯齿状病变') == '无蒂锯齿状病变 属于 锯齿状病变'
    assert rel_phrase('has_symptom', 'parent', '便血', '结直肠癌') == '结直肠癌 表现为 便血'
    assert rel_phrase('has_symptom', 'child', '结直肠癌', '便血') == '结直肠癌 表现为 便血'


@pytest.mark.integration
def test_concept_bundle_hides_rejected_relations_and_keeps_provenance():
    # 阵发性腹痛：被拒的“成人肠套叠 表现为 阵发性腹痛”不出现；词表归一后新增的“属于 腹痛”应出现
    colic = run(lambda db: KbwebDao.concept_bundle(db, COLIC_PAIN))
    assert not [r for r in colic['relations'] if r['rel_type'] == 'has_symptom']
    assert [r['name'] for r in colic['relations'] if r['rel_type'] == 'is_a'] == ['腹痛']
    blood = run(lambda db: KbwebDao.concept_bundle(db, BLOOD_IN_STOOL))
    diseases = {r['concept_id']: r for r in blood['relations'] if r['rel_type'] == 'has_symptom'}
    assert set(diseases) == {CRC, SRUS}
    assert all(r['dir'] == 'parent' for r in diseases.values())
    assert diseases[CRC]['source'] == 'manual' and '23267' in (diseases[SRUS]['note'] or '')


@pytest.mark.integration
def test_explore_concept_returns_relations_even_without_evidence():
    async def go():
        engine = create_async_engine(DSN)
        try:
            tools = {t.name: t for t in build_tools(async_sessionmaker(engine), graph=True)}
            return await tools['explore_concept'].ainvoke({'term': '便血'})
        finally:
            await engine.dispose()

    out = asyncio.run(go())
    assert '结直肠癌 表现为 便血' in out and '孤立性直肠溃疡综合征 表现为 便血（出处【23267】：“' in out and 'blood per rectum' in out
    assert '结直肠癌 表现为 便血（人工/AI 补录，无库内原文）' in out  # 人工/AI 补录，必须如实标出
    assert '便血 属于 消化道出血（受控词表（AI 审核 demo，非临床）' in out
    assert '没有与它关联的条目' not in out


@pytest.mark.integration
def test_literature_relations_link_to_original_text():
    # 来自文献（抽取或拆分审核）的关系必须能定位到原文片段；人工补录、词表、术语表结构关系允许暂时没有
    from sqlalchemy import text

    async def q(db):
        return (await db.execute(text("""
            SELECT r.relation_id FROM kb_concept_relation r
            WHERE r.review_status IS DISTINCT FROM 'rejected' AND r.source IN ('literature', 'review')
              AND NOT EXISTS (SELECT 1 FROM kb_span_link sl WHERE sl.entity_type = 'concept_relation' AND sl.entity_id = r.relation_id)"""))).scalars().all()

    assert run(q) == []


@pytest.mark.integration
def test_relations_carry_spans_with_page():
    blood = run(lambda db: KbwebDao.concept_bundle(db, BLOOD_IN_STOOL))
    srus = next(r for r in blood['relations'] if r['concept_id'] == SRUS)
    # 能定位 = 有文献 + 原文块（部分文献解析时没有页码，块仍可在全文页定位）
    sp = srus['spans'][0]
    assert sp['block_id'] and sp['record_id'] and sp['source_id'] == '23267' and 'blood per rectum' in sp['quote']
