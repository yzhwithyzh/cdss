"""
kbweb 只读查询的集成测试：直接跑真实 cdss 库（需本地 PostgreSQL）。
设置 KBWEB_TEST_DSN 覆盖连接串；连不上时整组跳过。
"""

import asyncio
import json
import os

import pytest
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from plugins.kbweb.dao.kbweb_dao import KbwebDao
from plugins.kbweb.service.kbweb_service import KbwebService, camel

pytestmark = pytest.mark.integration

DSN = os.getenv('KBWEB_TEST_DSN', 'postgresql+asyncpg://postgres:123456@127.0.0.1:5432/cdss')


def run(coro_fn):
    async def go():
        engine = create_async_engine(DSN)
        try:
            async with async_sessionmaker(engine)() as db:
                return await coro_fn(db)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(go())
    except OSError as e:
        pytest.skip(f'数据库不可用: {e}')


def page(**kw):
    return {'page_num': 1, 'page_size': 10, **kw}


def assert_json(data):
    # 返回值必须能直接被 JSON 序列化（不能混入 asyncpg Range 等对象）
    json.dumps(jsonable_encoder(data))


def test_stats_counts_match_tables():
    async def body(db):
        s = await KbwebDao.stats(db)
        n = (await db.execute(text("SELECT count(*) FROM lit_record WHERE del_flag = '0'"))).scalar()
        return s, n

    s, n = run(body)
    assert s['counts']['records'] == n
    assert s['counts']['structured_docs'] > 0
    assert {q['code'] for q in s['questions']} >= {'Q1', 'Q2', 'Q3', 'Q4', 'Q5'}
    assert s['top_concepts'] and s['top_concepts'][0]['n'] >= s['top_concepts'][-1]['n']
    assert_json(s)


def test_search_records_filters_and_facets():
    all_ = run(lambda db: KbwebDao.search_records(db, page()))
    structured = run(lambda db: KbwebDao.search_records(db, page(structured=True)))
    q1 = run(lambda db: KbwebDao.search_records(db, page(eq='Q1')))
    kw = run(lambda db: KbwebDao.search_records(db, page(q='adenoma', year_from=2015, year_to=2020)))
    assert structured['total'] == all_['facets']['structured'] > 0
    assert all(r['document_id'] for r in structured['rows'])
    assert all('Q1' in r['questions'] for r in q1['rows'])
    assert all(2015 <= r['pub_year'] <= 2020 for r in kw['rows'])
    assert sum(x['n'] for x in kw['facets']['year']) == kw['total']
    assert_json(kw)


def test_search_recommendations_details():
    d = run(lambda db: KbwebDao.search_recommendations(db, page(page_size=100)))
    assert d['total'] == len(d['rows']) > 0
    assert {r['strength'] for r in d['rows']} <= {'strong', 'weak', 'other', 'not_reported'}
    assert any(r['citations'] for r in d['rows'])
    assert any(r['actions'] for r in d['rows'])
    assert all('spans' in r for r in d['rows'])
    strong = run(lambda db: KbwebDao.search_recommendations(db, page(strength='strong', page_size=100)))
    assert strong['total'] == next(x['n'] for x in d['facets']['strength'] if x['key'] == 'strong')
    assert_json(d)


def test_search_findings_and_measure_filter():
    d = run(lambda db: KbwebDao.search_findings(db, page(measure='or')))
    assert d['total'] > 0
    assert all(any(x['measure'] == 'or' for x in r['results']) for r in d['rows'])
    assert all(r['record_id'] for r in d['rows'])
    assert_json(d)


def test_search_passages_snippet_contains_query():
    d = run(lambda db: KbwebDao.search_passages(db, page(q='surveillance')))
    assert d['total'] > 0
    assert all('surveillance' in r['snippet'].lower() for r in d['rows'])


def test_record_bundle_structured_and_plain():
    async def ids(db):
        s = (await db.execute(text(
            "SELECT record_id FROM kb_document d WHERE EXISTS (SELECT 1 FROM kb_edge e "
            "JOIN kb_document_version v ON v.version_id = e.version_id WHERE v.document_id = d.document_id AND e.origin = 'finding') LIMIT 1"))).scalar()
        g = (await db.execute(text('SELECT d.record_id FROM kb_guideline_document gd JOIN kb_document d USING (document_id) LIMIT 1'))).scalar()
        p = (await db.execute(text(
            'SELECT record_id FROM lit_record r WHERE NOT EXISTS (SELECT 1 FROM kb_document d WHERE d.record_id = r.record_id) LIMIT 1'))).scalar()
        return s, g, p

    s, g, p = run(ids)
    study = run(lambda db: KbwebDao.record_bundle(db, s))
    assert study['kb']['findings'] and study['kb']['studies']
    assert all(f['record_id'] == s for f in study['kb']['findings'])
    guide = run(lambda db: KbwebDao.record_bundle(db, g))
    assert guide['kb']['guideline'] and guide['kb']['recommendations']
    plain = run(lambda db: KbwebDao.record_bundle(db, p))
    assert plain['record']['title'] and plain['kb'] is None
    assert run(lambda db: KbwebDao.record_bundle(db, -1)) is None
    for x in (study, guide, plain):
        assert_json(camel(x))
    ft = run(lambda db: KbwebDao.record_fulltext(db, g))
    by_block = {b['block_id']: b['text'] for b in ft['blocks']}
    sp = [x for x in ft['spans'] if x['block_id'] in by_block]
    assert sp and all(0 <= x['char_start'] <= x['char_end'] <= len(by_block[x['block_id']]) for x in sp)


def test_record_bundle_graph_inputs():
    # 84790：RCT，有人群纳排条件、组别干预、结局、研究发现，均带原文溯源
    d = run(lambda db: KbwebDao.record_bundle(db, 84790))
    kb = d['kb']
    assert kb['studies'][0]['spans']
    assert any(o['spans'] for o in kb['outcomes'])
    assert any(c['spans'] for p in kb['populations'] for c in p['conditions'])
    assert any(x['spans'] for a in kb['arms'] for x in a['interventions'])
    assert all('exposure_arm_id' in f and 'comparator_arm_id' in f for f in kb['findings'])
    arm_ids = {a['arm_id'] for a in kb['arms']}
    assert any(f['exposure_arm_id'] in arm_ids for f in kb['findings'])
    # 文中用到的概念都要有类型与名称，供图谱着色
    used = {c['concept_id'] for p in kb['populations'] for c in p['conditions'] if c['concept_id']}
    used |= {o['concept_id'] for o in kb['outcomes'] if o['concept_id']}
    assert used and used <= set(kb['concepts'])
    assert all(kb['concepts'][c]['concept_type'] for c in used)
    for r in kb['concept_relations']:
        assert r['parent_id'] in kb['concepts'] and r['child_id'] in kb['concepts']
    assert_json(camel(d))


def test_concepts_and_concept_bundle():
    lst = run(lambda db: KbwebDao.concept_list(db, page(page_size=5)))
    assert lst['rows'] and all(r['n'] > 0 for r in lst['rows'])
    cid = lst['rows'][0]['concept_id']
    c = run(lambda db: KbwebDao.concept_bundle(db, cid))
    assert c['concept']['concept_id'] == cid
    assert c['findings'] or c['recommendations'] or c['assertions']
    typed = run(lambda db: KbwebDao.concept_list(db, page(concept_type='procedure')))
    assert all(r['concept_type'] == 'procedure' for r in typed['rows'])
    assert len(typed['facets']['concept_type']) > 1
    assert_json(c)


def test_question_bundle():
    d = run(lambda db: KbwebDao.question_bundle(db, 'Q1'))
    assert d['question']['records'] == sum(x['n'] for x in d['by_design'])
    assert run(lambda db: KbwebDao.question_bundle(db, 'NOPE')) is None
    assert_json(d)


def test_compare_and_suggest():
    d = run(lambda db: KbwebDao.compare(db, None, None))
    assert len(d['guidelines']) >= 2 and d['recommendations']
    s = run(lambda db: KbwebDao.suggest(db, 'polyp'))
    assert s and {x['type'] for x in s} <= {'concept', 'record'}
    assert run(lambda db: KbwebDao.suggest(db, '100%_')) is not None


def test_image_path_is_confined(monkeypatch):
    async def rid(db):
        return (await db.execute(text(
            "SELECT d.record_id FROM kb_block b JOIN kb_document_version v USING (version_id) "
            "JOIN kb_document d USING (document_id) WHERE b.extra ? 'img_path' LIMIT 1"))).scalar()

    monkeypatch.setenv('LIT_DATA_ROOT', 'E:/code/结直肠腺瘤项目')
    r = run(rid)
    assert run(lambda db: KbwebService.record_image(db, r, '../../../../RuoYi-FastAPI/ruoyi-fastapi-backend/.env.dev')) is None
