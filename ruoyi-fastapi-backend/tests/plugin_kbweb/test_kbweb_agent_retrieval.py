"""
问答智能体的确定性部分：参数归一、分词、BM25 题录检索、段落检索退化逻辑、引用提取。
数据库相关用例连真实 cdss 库（与 test_kbweb_dao 相同的跳过规则）；不调用大模型。
"""

import pytest

from plugins.kbweb.agent import retrieval as R
from plugins.kbweb.agent.agent import _as_list, cited_ids
from tests.plugin_kbweb.test_kbweb_dao import run


def test_as_list_accepts_model_quirks():
    assert _as_list(['a', ' b ', '']) == ['a', 'b']
    assert _as_list('["piecemeal EMR", "recurrence"]') == ['piecemeal EMR', 'recurrence']
    assert _as_list('cold snare, CSP；冷圈套') == ['cold snare', 'CSP', '冷圈套']
    assert _as_list(None) == []


def test_terms_of_normalizes_hyphen_plural_and_chinese():
    assert R.terms_of('Colorectal-cancer mortality') == ['colorectal', 'cancer', 'mortality']
    assert R.terms_of('adenomas polyps') == ['adenoma', 'polyp']
    assert R.terms_of('锯齿状病变') == ['锯齿', '齿状', '状病', '病变']
    assert 'the' not in R.terms_of('the risk of adenoma')


def test_cited_ids_dedup_in_order():
    assert cited_ids('见【21735】和【7641】，另见【21735】') == ['21735', '7641']


@pytest.mark.integration
def test_search_literature_bm25_matches_hyphenated_title():
    # 标题写 colorectal-cancer，检索词写 colorectal cancer，必须能命中且排在最前面
    rows = run(lambda db: R.search_literature(db, ['colorectal cancer mortality', 'adenoma removal', 'long-term'], limit=5))
    assert rows and rows[0]['source_id'] == '21684'


@pytest.mark.integration
def test_search_literature_filters():
    rows = run(lambda db: R.search_literature(db, ['piecemeal EMR', 'recurrence'], design='随机对照', year_from=2015, limit=10))
    assert rows and all('随机对照' in (r['design'] or '') and r['year'] >= 2015 for r in rows)


@pytest.mark.integration
def test_search_passages_falls_back_to_keywords_without_vector():
    rows = run(lambda db: R.search_passages(db, '不需要向量', ['piecemeal'], k=5, use_vector=False))
    assert rows and all('piecemeal' in r['text'].lower() for r in rows)


def test_fmt_lit_marks_structured_records():
    from plugins.kbweb.agent.agent import _fmt_lit

    out = _fmt_lit([{'record_id': 1, 'source_id': '9', 'year': 2020, 'design': 'RCT', 'title': 'A'},
                    {'record_id': 2, 'source_id': '8', 'year': None, 'design': None, 'title': 'B'}], {1})
    assert out.splitlines()[0] == '【9】2020 · RCT · 已结构化（可查全文） · A'
    assert '已结构化' not in out.splitlines()[1]


@pytest.mark.integration
def test_search_literature_tool_runs_end_to_end():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from plugins.kbweb.agent.agent import build_tools
    from tests.plugin_kbweb.test_kbweb_dao import DSN

    async def go():
        engine = create_async_engine(DSN)
        try:
            tools = {t.name: t for t in build_tools(async_sessionmaker(engine))}
            return await tools['search_literature'].ainvoke({'keywords': '["underwater cold snare", "polypectomy"]'})
        finally:
            await engine.dispose()

    out = asyncio.run(go())
    assert '【1043】' in out and '已结构化' in out


def test_excerpt_keeps_short_text_and_windows_long_text():
    from plugins.kbweb.agent.agent import _excerpt

    assert _excerpt('short text', ['x'], 100) == 'short text'
    long = 'intro ' * 300 + 'CPU time 3.6 seconds versus 154.4 seconds for ML' + ' tail' * 300
    out = _excerpt(long, ['CPU time', 'seconds'], 400)
    assert '3.6 seconds' in out and '154.4 seconds' in out and out.startswith('…') and len(out) <= 402


@pytest.mark.integration
def test_search_passages_distance_cutoff_drops_unrelated():
    related = run(lambda db: R.search_passages(db, 'underwater cold snare polypectomy R0 resection rate', k=10, max_distance=0.32))
    unrelated = run(lambda db: R.search_passages(db, 'how to bake sourdough bread at home', k=10, max_distance=0.32))
    assert related and all(r['distance'] <= 0.32 for r in related)
    assert unrelated == []


@pytest.mark.integration
def test_find_concepts_by_zh_en_and_alias():
    zh = run(lambda db: R.find_concepts(db, '阿司匹林'))
    en = run(lambda db: R.find_concepts(db, 'aspirin'))
    assert zh and zh[0]['concept_id'] == 4051 and en and en[0]['concept_id'] == 4051
    assert run(lambda db: R.find_concepts(db, 'zzzz-no-such-term')) == []


@pytest.mark.integration
def test_explore_concept_tool_aggregates_across_documents():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from plugins.kbweb.agent.agent import build_tools
    from tests.plugin_kbweb.test_kbweb_dao import DSN

    async def go():
        engine = create_async_engine(DSN)
        try:
            tools = {t.name: t for t in build_tools(async_sessionmaker(engine), graph=True)}
            assert 'explore_concept' not in {t.name for t in build_tools(async_sessionmaker(engine))}
            return await tools['explore_concept'].ainvoke({'term': '阿司匹林'})
        finally:
            await engine.dispose()

    out = asyncio.run(go())
    for sid in ('28515', '7358', '9218'):
        assert f'【{sid}】' in out
