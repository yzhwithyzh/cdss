import copy
import json
from pathlib import Path

import pytest

from tools.kb_pipeline.blocks import blocks_from_mineru, table_text
from tools.kb_pipeline.extract import DocInput, Extractor, LlmSettings, parse_json
from tools.kb_pipeline.prompts import format_blocks, system_prompt
from tools.kb_pipeline.validators import validate

BLOCKS = {
    'p4-10': 'The overall rate of histological complete resection was 91.8% in the cold EMR group and 95.9% in the '
    'EMR group (difference: 4.1%; 95%CI: -3.9-12.3).',
    'p4-11': 'Table 2 Outcomes\nOutcome | Cold EMR (n = 98) | EMR (n = 97)\nComplete resection | 90/98 (91.8) | 93/97 (95.9)',
}


def _good_output() -> dict:
    return {
        'schema_version': 'kb-extract-0.1',
        'document_version_id': '1',
        'evidence_spans': [
            {
                'span_id': 'S1',
                'block_id': 'p4-10',
                'page': 4,
                'quote': 'histological complete resection was 91.8% in the cold EMR group and 95.9% in the EMR group '
                '(difference: 4.1%; 95%CI: -3.9-12.3)',
            },
            {
                'span_id': 'S2',
                'block_id': 'p4-11',
                'page': 4,
                'quote': 'Complete resection | 90/98 (91.8) | 93/97 (95.9)',
            },
        ],
        'document': {'doc_kind': 'original_research', 'subject_scope': 'human', 'triage_uncertainty': 'low'},
        'studies': [
            {
                'study_ref': 'ST1',
                'design_coarse': 'rct',
                'design_uncertainty': 'low',
                'unit_of_analysis_default': 'lesion',
            }
        ],
        'populations': [
            {
                'population_ref': 'P1',
                'study_ref': 'ST1',
                'role': 'overall',
                'label': '5-10 mm 腺瘤',
                'age': {'state': 'not_reported'},
                'indication_setting': 'not_reported',
                'eligibility_conditions': [
                    {
                        'concept': {'text': 'polyp size', 'concept_type': 'population_trait'},
                        'qualifier': 'measured',
                        'quantity': {
                            'op': 'range',
                            'value_low': 5,
                            'value_high': 10,
                            'unit': 'mm',
                            'as_reported': '5-10 mm',
                        },
                        'span_ids': ['S1'],
                    }
                ],
                'span_ids': ['S1'],
            }
        ],
        'arms': [
            {
                'arm_ref': 'A1',
                'study_ref': 'ST1',
                'label': 'cold EMR',
                'role': 'intervention',
                'interventions': [
                    {'concept': {'text': 'cold EMR', 'concept_type': 'procedure'}, 'details_as_reported': 'cold EMR'}
                ],
            },
            {
                'arm_ref': 'A2',
                'study_ref': 'ST1',
                'label': 'EMR',
                'role': 'control',
                'interventions': [
                    {'concept': {'text': 'EMR', 'concept_type': 'procedure'}, 'details_as_reported': 'EMR'}
                ],
            },
        ],
        'outcomes': [
            {
                'outcome_ref': 'O1',
                'concept': {'text': 'complete resection', 'concept_type': 'outcome_event'},
                'label_as_reported': 'histological complete resection',
                'timepoint': {'state': 'not_applicable'},
                'primacy': 'primary',
            }
        ],
        'findings': [
            {
                'finding_ref': 'F1',
                'study_ref': 'ST1',
                'statement_role': 'own_result',
                'finding_type': 'comparative_effect',
                'population_ref': 'P1',
                'unit_of_analysis': 'lesion',
                'exposure': {'arm_ref': 'A1'},
                'comparator': {'arm_ref': 'A2'},
                'outcome_ref': 'O1',
                'results': [
                    {
                        'measure': 'proportion',
                        'arm_ref': 'A1',
                        'value_as_reported': '90/98 (91.8)',
                        'value': 91.8,
                        'numerator': 90,
                        'denominator': 98,
                        'unit': '%',
                    },
                    {
                        'measure': 'rd',
                        'arm_ref': None,
                        'value_as_reported': '4.1%; 95%CI: -3.9-12.3',
                        'value': 4.1,
                        'ci_low': -3.9,
                        'ci_high': 12.3,
                        'unit': '%',
                    },
                ],
                'source_span_ids': ['S1', 'S2'],
            }
        ],
    }


def _codes(doc: dict) -> list[str]:
    return sorted(i.rule_code for i in validate(doc, BLOCKS))


def test_good_output_has_no_issues() -> None:
    assert _codes(_good_output()) == []


def test_fabricated_quote_and_unknown_block_are_errors() -> None:
    doc = _good_output()
    doc['evidence_spans'][0]['quote'] = 'complete resection was 99.9% in the cold EMR group'
    doc['evidence_spans'][1]['block_id'] = 'p9-99'
    # 编造的片段里没有 4.1 等数值，结果数值随之也对不上所引片段
    assert _codes(doc) == ['block_not_found', 'number_not_in_span', 'quote_not_found', 'value_not_verbatim']


def test_quote_in_other_block_is_warning() -> None:
    doc = _good_output()
    doc['evidence_spans'][1]['block_id'] = 'p4-10'
    assert _codes(doc) == ['quote_wrong_block']


def test_numeric_checks() -> None:
    doc = _good_output()
    r0, r1 = doc['findings'][0]['results']
    r0['numerator'], r0['value'] = 99, 91.8  # n > N，且百分比与 n/N 不符
    r1['ci_low'], r1['ci_high'] = 5.0, 12.3  # 点估计不在区间内
    r1['value_as_reported'] = '4.7%'  # 片段中没有 4.7
    # 99 不在原文片段中 → 同时提示“计数可能是推算补全”；'4.7%' 不是原文 → value_not_verbatim
    assert _codes(doc) == [
        'ci_excludes_estimate',
        'count_not_in_span',
        'n_gt_N',
        'number_not_in_span',
        'pct_mismatch',
        'value_not_verbatim',
    ]


def test_value_with_added_label_is_not_verbatim() -> None:
    doc = _good_output()
    doc['findings'][0]['results'][0]['value_as_reported'] = 'P = 90/98 (91.8)'  # 原文没有 “P =”
    assert 'value_not_verbatim' in _codes(doc)
    doc['findings'][0]['results'][0]['value_as_reported'] = '90/98 (91.8)'
    assert 'value_not_verbatim' not in _codes(doc)


def test_ci_level_not_reported_as_missing_number() -> None:
    doc = _good_output()
    doc['findings'][0]['results'][1]['value_as_reported'] = '4.1 (95% CI -3.9 to 12.3)'
    doc['evidence_spans'][0]['quote'] = 'difference: 4.1%; 95%CI: -3.9-12.3'
    assert 'number_not_in_span' not in _codes(doc)


def test_dangling_refs_and_schema_violations() -> None:
    doc = _good_output()
    doc['findings'][0]['outcome_ref'] = 'O9'
    doc['findings'][0]['risk_of_bias'] = 'low'  # LLM 自造字段
    codes = _codes(doc)
    assert 'ref_undefined' in codes
    assert 'schema_invalid' in codes


def test_parse_json_strips_fences() -> None:
    assert parse_json('说明\n```json\n{"a": 1}\n```') == {'a': 1}


def test_table_text_unescapes_entities() -> None:
    html = '<table><tr><td>P</td><td>&lt;0.001</td></tr></table>'
    assert table_text(html) == 'P | <0.001'


def test_continuation_block_merged_into_previous_paragraph() -> None:
    content = [
        {'type': 'text', 'text': 'Results', 'text_level': 1, 'bbox': [100, 100, 400, 120], 'page_idx': 0},
        {
            'type': 'text',
            'text': 'First half of a paragraph that continues',
            'bbox': [100, 130, 480, 900],
            'page_idx': 0,
        },
        {'type': 'header', 'text': 'Journal', 'bbox': [0, 0, 100, 10], 'page_idx': 1},
        {'type': 'text', 'text': '', 'bbox': [520, 60, 900, 300], 'page_idx': 1},
    ]
    blocks = blocks_from_mineru(content)
    assert [b.block_type for b in blocks] == ['heading', 'paragraph']
    assert blocks[1].section == 'results'
    assert blocks[1].page == 1 and blocks[1].page_end == 2  # noqa: PLR2004
    assert len(blocks[1].regions) == 2  # noqa: PLR2004


def _doc() -> DocInput:
    blocks = [
        {'block_key': k, 'section': 'results', 'block_type': 'paragraph', 'page': 4, 'text': t}
        for k, t in BLOCKS.items()
    ]
    return DocInput('2800', 1, 'sha', 'mineru', {'title': 't', 'year': 2021, 'design_label': '随机对照试验'}, blocks)


def _settings() -> LlmSettings:
    return LlmSettings('http://x', '', 'mock-model', 60, True, 1, 0, 0)


def test_extractor_repairs_once_after_errors(tmp_path: Path) -> None:
    bad = copy.deepcopy(_good_output())
    bad['evidence_spans'][0]['quote'] = '编造的原文'
    (tmp_path / '2800.json').write_text(json.dumps(bad, ensure_ascii=False), encoding='utf-8')
    (tmp_path / '2800.r1.json').write_text(json.dumps(_good_output(), ensure_ascii=False), encoding='utf-8')

    result = Extractor(_settings(), mock_dir=tmp_path).run(_doc())

    assert result.status == 'to_review'
    assert result.rounds == 2  # noqa: PLR2004
    assert result.issues == []
    assert result.tokens_in > 0


def test_extractor_reports_invalid_json(tmp_path: Path) -> None:
    (tmp_path / '2800.json').write_text('这不是 JSON', encoding='utf-8')
    result = Extractor(_settings(), mock_dir=tmp_path).run(_doc())
    assert result.status == 'failed'
    assert result.error.startswith('JSON 解析失败')


def test_extractor_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match='没有可用的模型 Key'):
        Extractor(_settings())


def test_prompt_contains_block_ids_and_schema() -> None:
    text = format_blocks(_doc().blocks)
    assert text.startswith('[p4-10|results|paragraph|p4]')
    sp = system_prompt()
    assert 'evidence_spans' in sp
    assert 'AA-v2' in sp


def test_repeated_measures_need_category_or_arm() -> None:
    doc = _good_output()
    f = doc['findings'][0]
    f['results'] = [
        {'measure': 'proportion', 'arm_ref': None, 'value_as_reported': '90/98 (91.8)', 'value': 91.8, 'unit': '%'},
        {'measure': 'proportion', 'arm_ref': None, 'value_as_reported': '93/97 (95.9)', 'value': 95.9, 'unit': '%'},
    ]
    assert 'finding_mixed' in _codes(doc)  # 两个比例无法区分
    f['results'][0]['category'] = '5-7 mm'
    f['results'][1]['category'] = '8-10 mm'
    assert 'finding_mixed' not in _codes(doc)
    f['results'][1]['category'] = '5-7 mm'  # 类别重复
    assert 'finding_mixed' in _codes(doc)


def test_denominator_from_table_header_is_info_not_warning() -> None:
    doc = _good_output()
    doc['evidence_spans'][1]['quote'] = 'Complete resection | 90/98 (91.8)'
    r0 = doc['findings'][0]['results'][0]
    r0['value_as_reported'], r0['numerator'], r0['denominator'] = '90/98 (91.8)', 90, 98
    doc['findings'][0]['source_span_ids'] = ['S1', 'S2']
    # 数据行里本身有 98：正常
    assert 'count_not_in_span' not in _codes(doc)

    doc['evidence_spans'][1]['quote'] = 'Complete resection | 90/98'
    r0['value_as_reported'], r0['numerator'], r0['denominator'] = '90', 90, 97  # 97 只在表头 “EMR (n = 97)”
    codes = _codes(doc)
    assert 'count_from_table_header' in codes
    assert 'count_not_in_span' not in codes

    r0['denominator'] = 46  # 原文任何位置都没有 46：仍是推算补全
    assert 'count_not_in_span' in _codes(doc)


def test_denominator_from_group_size_in_body_text() -> None:
    doc = _good_output()
    blocks = dict(BLOCKS, **{'p2-5': 'The final analysis included 89 patients with 122 polyps in the UCSP group.'})
    r0 = doc['findings'][0]['results'][0]
    r0['value_as_reported'], r0['numerator'], r0['denominator'] = '90', 90, 122  # 122 只在正文“122 polyps”
    codes = sorted(i.rule_code for i in validate(doc, blocks))
    assert 'count_from_group_size' in codes
    assert 'count_not_in_span' not in codes


def _write_chunked_mocks(tmp_path: Path, chunk2: str | None = None) -> None:
    good = _good_output()
    skeleton = dict(good, findings=[], effect_modifications=[])
    c1 = {
        'evidence_spans': [{'span_id': 'S1001', 'block_id': 'p4-10', 'quote': 'difference: 4.1%; 95%CI: -3.9-12.3'}],
        'findings': [
            dict(
                good['findings'][0],
                finding_ref='F1001',
                source_span_ids=['S1001'],
                results=[good['findings'][0]['results'][1]],
            )
        ],
    }
    c2 = {
        'evidence_spans': [{'span_id': 'S2001', 'block_id': 'p4-11', 'quote': 'Complete resection | 90/98 (91.8)'}],
        'findings': [
            dict(
                good['findings'][0],
                finding_ref='F2001',
                source_span_ids=['S2001'],
                results=[good['findings'][0]['results'][0]],
            )
        ],
    }
    (tmp_path / '2800.json').write_text('<<TRUNCATED>>{"partial": ', encoding='utf-8')
    (tmp_path / '2800.skeleton.json').write_text(json.dumps(skeleton, ensure_ascii=False), encoding='utf-8')
    (tmp_path / '2800.c1.json').write_text(json.dumps(c1, ensure_ascii=False), encoding='utf-8')
    (tmp_path / '2800.c2.json').write_text(chunk2 or json.dumps(c2, ensure_ascii=False), encoding='utf-8')


def test_truncated_output_falls_back_to_chunked_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.kb_pipeline.extract as ext  # noqa: PLC0415

    monkeypatch.setattr(ext, 'CHUNK_TOKENS', 30)  # 两个原文块各成一段
    _write_chunked_mocks(tmp_path)

    result = Extractor(_settings(), mock_dir=tmp_path).run(_doc())

    assert result.status == 'to_review'
    assert [f['finding_ref'] for f in result.output['findings']] == ['F1001', 'F2001']
    assert {s['span_id'] for s in result.output['evidence_spans']} >= {'S1', 'S2', 'S1001', 'S2001'}
    codes = [i.rule_code for i in result.issues]
    assert 'chunked_extraction' in codes
    assert not [i for i in result.issues if i.severity == 'error']
    assert result.rounds == 4  # noqa: PLR2004  整篇(截断) + 骨架 + 2 段


def test_failed_chunk_is_reported_but_others_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.kb_pipeline.extract as ext  # noqa: PLC0415

    monkeypatch.setattr(ext, 'CHUNK_TOKENS', 30)
    _write_chunked_mocks(tmp_path, chunk2='不是 JSON')

    result = Extractor(_settings(), mock_dir=tmp_path).run(_doc())

    assert result.status == 'to_review'
    assert [f['finding_ref'] for f in result.output['findings']] == ['F1001']
    note = next(i for i in result.issues if i.rule_code == 'chunked_extraction')
    assert note.severity == 'warn'
    assert '失败段' in note.message


def test_population_condition_only_in_label_is_error() -> None:
    doc = _good_output()
    doc['populations'][0].pop('eligibility_conditions')
    assert 'population_unstructured' in _codes(doc)


def test_english_number_words_count_as_numbers() -> None:
    doc = _good_output()
    doc['evidence_spans'][1]['quote'] = 'Complete resection | 90/98 (91.8)'
    r0 = doc['findings'][0]['results'][0]
    r0['value_as_reported'], r0['numerator'], r0['denominator'] = '90/98 (91.8)', 90, 98
    blocks = dict(BLOCKS, **{'p4-12': 'Nine patients had bleeding.'})
    doc['evidence_spans'].append({'span_id': 'S3', 'block_id': 'p4-12', 'quote': 'Nine patients had bleeding.'})
    doc['findings'][0]['source_span_ids'].append('S3')
    doc['findings'][0]['results'].append(
        {'measure': 'count', 'arm_ref': 'A2', 'value_as_reported': 'Nine', 'value': 9, 'numerator': 9}
    )
    assert 'count_not_in_span' not in sorted(i.rule_code for i in validate(doc, blocks))
