"""
症状归一的确定性部分：并列拆分、同义词精确匹配（不调大模型）。
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.plugin_kbweb.test_kbweb_dao import DSN
from tools.kb_pipeline.normalize import SymptomNormalizer, split_term


def test_split_term_only_on_punctuation():
    assert split_term('腹胀、腹痛、便秘') == ['腹胀', '腹痛', '便秘']
    assert split_term('tenesmus, straining, incontinence') == ['tenesmus', 'straining', 'incontinence']
    # 连词不由规则拆：是否复合交给模型（“便秘和腹泻交替”是一个症状）
    assert split_term('便秘和腹泻交替') == ['便秘和腹泻交替']
    assert split_term('血便或黑便') == ['血便或黑便']
    assert split_term('术后并发症') == ['术后并发症']


@pytest.fixture(scope='module')
def normalizer():
    async def go():
        engine = create_async_engine(DSN)
        try:
            async with async_sessionmaker(engine)() as db:
                return await SymptomNormalizer.load(db, with_vectors=False)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(go())
    except OSError as e:
        pytest.skip(f'数据库不可用: {e}')


@pytest.mark.integration
def test_vocabulary_loaded_with_hierarchy(normalizer):
    assert len(normalizer.entries) >= 31
    assert 'SYM:GI_BLEEDING' in normalizer.by_code['SYM:MELENA'].parents


@pytest.mark.integration
def test_exact_synonyms_and_confusables(normalizer):
    def code(t):
        e = normalizer.exact(t)
        return e.code if e else None

    assert code('大便带血') == 'SYM:HEMATOCHEZIA'
    assert code('rectal bleeding') == 'SYM:HEMATOCHEZIA'
    assert code('柏油样便') == 'SYM:MELENA'
    assert code('黑便') == 'SYM:MELENA'  # 不能落到便血
    assert code('贫血') == 'SYM:ANEMIA' and code('缺铁性贫血') == 'SYM:IDA'
    assert code('脱肛') == 'SYM:ANAL_PROTRUSION'
    assert code('直肠脱垂') is None  # 疾病，不在症状词表


@pytest.mark.integration
def test_compound_term_splits_into_exact_matches(normalizer):
    out = asyncio.run(normalizer.normalize_term('血便、黑便'))
    assert [(m.code, m.method) for m in out] == [('SYM:HEMATOCHEZIA', 'exact'), ('SYM:MELENA', 'exact')]
    assert not any(m.needs_review for m in out)


@pytest.mark.integration
def test_recall_with_parent_concepts_does_not_crash(normalizer):
    # 回归：候选带上位概念（便血 → 消化道出血）时曾因 dataclass 不可哈希报错
    cands = asyncio.run(normalizer.recall('解血便'))
    codes = [e.code for e, _ in cands]
    assert 'SYM:HEMATOCHEZIA' in codes and 'SYM:GI_BLEEDING' in codes and len(codes) == len(set(codes))


@pytest.mark.integration
def test_disease_names_are_not_symptoms(normalizer):
    for t in ('直肠脱垂', '结直肠癌', '溃疡性结肠炎', '痔'):
        out = asyncio.run(normalizer.normalize_term(t))
        assert [(m.relation, m.code) for m in out] == [('NOT_SYMPTOM', None)], t
    # 症状同义词优先于疾病：“脱肛”是症状
    assert asyncio.run(normalizer.normalize_term('脱肛'))[0].code == 'SYM:ANAL_PROTRUSION'
