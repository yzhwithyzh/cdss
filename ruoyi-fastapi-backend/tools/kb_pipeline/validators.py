"""
抽取结果的程序校验：JSON Schema、局部引用完整性、原文片段逐字核对、数值一致性

校验只产生问题清单（issue），不修改抽取结果。
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

import jsonschema

from tools.kb_pipeline.prompts import load_schema

_VALIDATOR = jsonschema.Draft202012Validator(load_schema())
PCT_TOLERANCE = 0.5  # 百分比与 n/N 的允许误差（百分点）


@dataclass
class Issue:
    """
    校验问题
    """

    rule_code: str
    severity: str  # info / warn / error
    entity_type: str
    entity_ref: str | None
    message: str


def normalize(text: str) -> str:
    """
    逐字核对前的归一化：全角→半角、去空白、统一连字符
    """
    t = unicodedata.normalize('NFKC', text or '')
    t = re.sub(r'[‐‑‒–—−－]', '-', t)
    return re.sub(r'\s+', '', t).lower()


def schema_issues(doc: dict) -> list[Issue]:
    out = []
    for e in _VALIDATOR.iter_errors(doc):
        path = '.'.join(str(p) for p in e.absolute_path)
        out.append(Issue('schema_invalid', 'error', 'document', path or None, e.message[:300]))
    return out


def ref_issues(doc: dict) -> list[Issue]:
    """
    局部引用（*_ref、span_ids）必须指向已定义的对象
    """
    defined = {
        'span': {s.get('span_id') for s in doc.get('evidence_spans', [])},
        'study': {s.get('study_ref') for s in doc.get('studies', [])},
        'population': {p.get('population_ref') for p in doc.get('populations', [])},
        'arm': {a.get('arm_ref') for a in doc.get('arms', [])},
        'outcome': {o.get('outcome_ref') for o in doc.get('outcomes', [])},
        'finding': {f.get('finding_ref') for f in doc.get('findings', [])},
    }
    key_kind = {
        'study_ref': 'study',
        'population_ref': 'population',
        'parent_population_ref': 'population',
        'arm_ref': 'arm',
        'outcome_ref': 'outcome',
        'parent_finding_ref': 'finding',
        'overall_finding_ref': 'finding',
    }
    owner = {'studies': 'study_ref', 'populations': 'population_ref', 'arms': 'arm_ref', 'outcomes': 'outcome_ref'}
    out: list[Issue] = []

    def check(kind: str, value: object, path: str) -> None:
        if value is not None and not isinstance(value, str):  # 模型把对象放进了引用位置
            out.append(Issue('ref_malformed', 'error', kind, None, f'{path} 应为引用 ID，实际为 {str(value)[:80]}'))
            return
        if value and value not in defined[kind]:
            out.append(Issue('ref_undefined', 'error', kind, value, f'{path} 引用了未定义的 {kind} {value}'))

    def walk(node: object, path: str, top: str | None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                p = f'{path}.{k}'
                if k.endswith('span_ids') and isinstance(v, list):
                    for s in v:
                        check('span', s, p)
                elif k == 'subgroup_finding_refs' and isinstance(v, list):
                    for f in v:
                        check('finding', f, p)
                elif (
                    k in key_kind and isinstance(v, str) and not (top and owner.get(top) == k and path.count('.') == 1)
                ):
                    check(key_kind[k], v, p)
                walk(v, p, top)
        elif isinstance(node, list):
            for i, x in enumerate(node):
                walk(x, f'{path}[{i}]', top)

    for top_key, value in doc.items():
        walk(value, top_key, top_key)
    return out


def quote_issues(doc: dict, blocks: dict[str, str]) -> list[Issue]:
    """
    原文片段必须逐字出现在所引用的块中（归一化后子串匹配）

    :param blocks: 块 ID → 块文本
    """
    out = []
    norm_cache: dict[str, str] = {}
    for s in doc.get('evidence_spans', []):
        sid, bid, quote = s.get('span_id'), s.get('block_id'), s.get('quote') or ''
        if bid not in blocks:
            out.append(Issue('block_not_found', 'error', 'span', sid, f'块 {bid} 不存在'))
            continue
        if bid not in norm_cache:
            norm_cache[bid] = normalize(blocks[bid])
        q = normalize(quote)
        if q and q not in norm_cache[bid]:
            hit = next((k for k, t in blocks.items() if q in norm_cache.setdefault(k, normalize(t))), None)
            if hit:
                out.append(Issue('quote_wrong_block', 'warn', 'span', sid, f'片段不在 {bid}，实际在 {hit}'))
            else:
                out.append(Issue('quote_not_found', 'error', 'span', sid, f'片段在原文中找不到: {quote[:80]}'))
    return out


_NUMBER_WORDS = {
    w: str(i)
    for i, w in enumerate(
        [
            'zero',
            'one',
            'two',
            'three',
            'four',
            'five',
            'six',
            'seven',
            'eight',
            'nine',
            'ten',
            'eleven',
            'twelve',
            'thirteen',
            'fourteen',
            'fifteen',
            'sixteen',
            'seventeen',
            'eighteen',
            'nineteen',
            'twenty',
        ]
    )
} | {'thirty': '30', 'forty': '40', 'fifty': '50', 'sixty': '60', 'seventy': '70', 'eighty': '80', 'ninety': '90'}


def _numbers(text: str) -> set[str]:
    """
    文本中的数字（含英文数词 one…twenty、thirty…ninety，如 “Nine patients” → 9）
    """
    t = normalize(text)
    words = {_NUMBER_WORDS[w] for w in re.findall(r'[a-z]+', (text or '').lower()) if w in _NUMBER_WORDS}
    return set(re.findall(r'\d+(?:\.\d+)?', t)) | words


_SIZE_IN_LABEL = re.compile(r'\d+(?:\.\d+)?\s*(?:mm|cm|毫米|厘米)', re.IGNORECASE)
_AGE_IN_LABEL = re.compile(r'\d+\s*(?:岁|周岁|years?|yrs?)(?![a-z])', re.IGNORECASE)


def population_issues(doc: dict) -> list[Issue]:
    """
    人群 label 中写了大小/年龄等数值条件，却没有对应的结构化条件或年龄字段 → 退回补全（规则 8a）
    """
    out = []
    for p in doc.get('populations') or []:
        label = p.get('label') or ''
        conditions = [
            *(p.get('eligibility_conditions') or []),
            *(p.get('exclusion_conditions') or []),
            *(p.get('subgroup_conditions') or []),
        ]
        units = {((c.get('quantity') or {}).get('unit') or '').lower() for c in conditions}
        missing = []
        if _SIZE_IN_LABEL.search(label) and not units & {'mm', 'cm', '毫米', '厘米'}:
            missing.append('病灶大小')
        if (
            _AGE_IN_LABEL.search(label)
            and (p.get('age') or {}).get('state') != 'specified'
            and not units & {'year', 'years', '岁'}
        ):
            missing.append('年龄')
        if missing:
            out.append(
                Issue(
                    'population_unstructured',
                    'error',
                    'population',
                    p.get('population_ref'),
                    f'人群 {label[:60]!r} 中的{"、".join(missing)}条件只写在 label 里：请写入 eligibility_conditions /'
                    ' subgroup_conditions（concept + quantity）或 age 字段',
                )
            )
    return out


_CONCEPT_COMPARATOR = re.compile(r'[≥≤<>＜＞≧≦=]|%')
_CONCEPT_QUANTITY = re.compile(
    r'\d+(?:\.\d+)?\s*(?:mm|cm|kg|mg|ml|岁|周岁|毫米|厘米|个|枚|次|天|周|月|年)(?![A-Za-z])'
    r'|\d+(?:\.\d+)?\s*(?:years?|yrs?|months?|weeks?|days?|pills?)\b'
    r'|(?:less|more|greater|fewer) than\s*\d|at least\s*\d|\d\s*(?:以上|以下)|(?:超过|不足|大于|小于|至少)\s*\d',
    re.IGNORECASE,
)
_CONCEPT_LIST = re.compile(r'(?<!\d),|,(?!\d)|[，、;；]')  # 化学名中数字间的逗号（16,16-dimethyl）不算


def concept_issues(doc: dict) -> list[Issue]:
    """
    概念 text 只能是一个概念的名称（规则 11a）：带数值/比较符/限定条件、或多个概念用逗号并列 → 退回修改。
    否则同一概念会因限定条件不同被建成很多个候选概念，按概念检索时漏掉
    """
    out: list[Issue] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            text = node.get('text')
            if isinstance(text, str) and 'concept_type' in node:
                problems = []
                if _CONCEPT_COMPARATOR.search(text) or _CONCEPT_QUANTITY.search(text):
                    problems.append('含数值或比较条件（应写入 quantity / level / category）')
                if _CONCEPT_LIST.search(text):
                    problems.append('多个概念并列（应拆开分别登记）')
                if problems:
                    out.append(
                        Issue(
                            'concept_not_atomic',
                            'error',
                            'concept',
                            path,
                            f'概念 {text[:60]!r} {"；".join(problems)}，text 只写一个概念的名称',
                        )
                    )
            for k, v in node.items():
                walk(v, f'{path}.{k}')
        elif isinstance(node, list):
            for i, x in enumerate(node):
                walk(x, f'{path}[{i}]')

    for key, value in doc.items():
        walk(value, key)
    return out


_N_LABEL = re.compile(r'\bn=(\d+)')  # 归一化后的 “n = 122”“(N=98)”
# 原文中明确作为样本量书写的数字：“122 polyps”“89 patients”“181例”“195个息肉”
_GROUP_SIZE = re.compile(
    r'(\d+)(?:polyps?|patients?|lesions?|subjects?|participants?|cases?|adenomas?|individuals?|persons?|'
    r'colonoscop(?:y|ies)|procedures?|例|名|人|个息肉|枚|个病灶)'
)


def header_counts(block_text: str) -> set[str]:
    """
    原文块中以 n=… 标注的样本量（如表头“UCSP (n = 122)”）
    """
    return set(_N_LABEL.findall(normalize(block_text)))


def document_group_sizes(blocks: dict[str, str]) -> set[str]:
    """
    全文中明确书写为样本量的数字（n=… 或 数字+人/例/息肉等单位词），作为分母的合法来源
    """
    sizes: set[str] = set()
    for text in blocks.values():
        t = normalize(text)
        sizes |= set(_N_LABEL.findall(t)) | set(_GROUP_SIZE.findall(t))
    return sizes


def count_source_issues(
    r: dict, fid: str | None, cited_numbers: set[str], context_counts: set[str], doc_sizes: set[str]
) -> list[Issue]:
    """
    分子/分母须在所引原文中出现。分母不在数据行时，按来源分级：
    1. 所引原文块中的 n= 标注（如表头 “UCSP (n = 122)”）→ info count_from_table_header
    2. 全文其他位置明确书写的样本量（如正文 “122 polyps in the UCSP group”）→ info count_from_group_size
    3. 都找不到 → warn count_not_in_span（可能是由百分比反推或编造）
    入库时 kb_edge.qualifiers.denominator_source 记录分母来源。
    """
    header, group, inferred = [], [], []
    for name, v in (('numerator', r.get('numerator')), ('denominator', r.get('denominator'))):
        if v is None or str(v) in cited_numbers:
            continue
        if name == 'denominator' and str(v) in context_counts:
            header.append(f'{name}={v}')
        elif name == 'denominator' and str(v) in doc_sizes:
            group.append(f'{name}={v}')
        else:
            inferred.append(f'{name}={v}')
    reported = r.get('value_as_reported')
    out = []
    if header:
        out.append(
            Issue('count_from_table_header', 'info', 'finding', fid, f'{reported!r} 的 {header} 取自所在块的 n= 标注')
        )
    if group:
        out.append(
            Issue('count_from_group_size', 'info', 'finding', fid, f'{reported!r} 的 {group} 取自原文报告的组样本量')
        )
    if inferred:
        out.append(
            Issue(
                'count_not_in_span',
                'warn',
                'finding',
                fid,
                f'{reported!r} 的 {inferred} 在原文中找不到，可能是推算补全',
            )
        )
    return out


def numeric_issues(doc: dict, blocks: dict[str, str] | None = None) -> list[Issue]:
    """
    数值一致性：n≤N、百分比与 n/N、置信区间包含点估计、数值能在所引片段中找到

    :param blocks: 块 ID → 块文本；用于识别取自表头 n= 标注的分母
    """
    out = []
    blocks = blocks or {}
    spans = {s.get('span_id'): s.get('quote') or '' for s in doc.get('evidence_spans', [])}
    span_block = {s.get('span_id'): s.get('block_id') for s in doc.get('evidence_spans', [])}
    doc_sizes = document_group_sizes(blocks)
    for f in doc.get('findings', []):
        fid = f.get('finding_ref')
        quotes = [normalize(spans.get(s, '')) for s in f.get('source_span_ids', [])]
        cited = ''.join(spans.get(s, '') for s in f.get('source_span_ids', []))
        cited_numbers = _numbers(cited)
        context_counts: set[str] = set()
        for s in f.get('source_span_ids', []):
            context_counts |= header_counts(blocks.get(span_block.get(s), ''))
        # 粒度：同一种 result 出现多次时，必须能用 arm_ref 或 category 区分，否则无法知道各数值对应什么
        keys = Counter(
            (r.get('measure'), r.get('arm_ref') or None, (r.get('category') or '').strip() or None)
            for r in f.get('results', [])
        )
        repeated = sorted(
            {
                m
                for (m, arm, cat), c in keys.items()
                if c > 1 or (arm is None and cat is None and sum(v for (m2, _, _), v in keys.items() if m2 == m) > 1)
            }
        )
        if repeated:
            out.append(
                Issue(
                    'finding_mixed',
                    'error',
                    'finding',
                    fid,
                    f'同一 finding 中 {repeated} 出现多次但未用 arm_ref/category 区分：同一变量的不同类别请填写 category，'
                    '不同因素/结局/亚组请拆成不同 finding，P 值写入 p_as_reported',
                )
            )
        for r in f.get('results', []):
            reported = normalize(r.get('value_as_reported') or '')
            # 原样保留：value_as_reported 必须是某个所引片段中的连续子串
            if reported and quotes and not any(reported in q for q in quotes):
                out.append(
                    Issue(
                        'value_not_verbatim',
                        'error',
                        'finding',
                        fid,
                        f'value_as_reported {r.get("value_as_reported")!r} 不是所引片段中的原文，请逐字摘录',
                    )
                )
            n, big_n, value = r.get('numerator'), r.get('denominator'), r.get('value')
            lo, hi = r.get('ci_low'), r.get('ci_high')
            if n is not None and big_n is not None and n > big_n:
                out.append(Issue('n_gt_N', 'error', 'finding', fid, f'{r.get("measure")}: n={n} > N={big_n}'))
            if (
                n is not None
                and big_n
                and value is not None
                and r.get('unit') == '%'
                and abs(n / big_n * 100 - value) > PCT_TOLERANCE
            ):
                out.append(
                    Issue(
                        'pct_mismatch',
                        'warn',
                        'finding',
                        fid,
                        f'{n}/{big_n}={n / big_n * 100:.1f}% 与报告值 {value}% 不一致',
                    )
                )
            if None not in (lo, hi, value) and not (min(lo, hi) <= value <= max(lo, hi)):
                out.append(
                    Issue('ci_excludes_estimate', 'warn', 'finding', fid, f'点估计 {value} 不在 [{lo}, {hi}] 内')
                )
            if None not in (lo, hi) and lo > hi:
                out.append(Issue('ci_reversed', 'warn', 'finding', fid, f'置信区间下限 {lo} > 上限 {hi}'))
            ci_levels = {'90', '95', '99'}  # “95% CI” 中的置信水平不算结果数值
            missing = [
                x for x in _numbers(r.get('value_as_reported') or '') if x not in cited_numbers and x not in ci_levels
            ]
            if cited:
                out += count_source_issues(r, fid, cited_numbers, context_counts, doc_sizes)
            if missing and cited:
                out.append(
                    Issue(
                        'number_not_in_span',
                        'warn',
                        'finding',
                        fid,
                        f'{r.get("value_as_reported")!r} 中的 {missing} 不在所引原文片段中',
                    )
                )
    return out


def completeness_issues(doc: dict) -> list[Issue]:
    """
    必填关系：结果必须关联结局；指南必须有基本信息
    """
    out = [
        Issue('finding_no_outcome', 'error', 'finding', f.get('finding_ref'), '结果没有关联结局 outcome_ref')
        for f in doc.get('findings') or []
        if not f.get('outcome_ref') and f.get('finding_type') != 'case_observation'
    ]
    kind = (doc.get('document') or {}).get('doc_kind')
    if kind in ('guideline', 'consensus_or_position') and not doc.get('guideline_info'):
        out.append(Issue('guideline_info_missing', 'error', 'document', None, '指南/共识缺 guideline_info'))
    return out


def validate(doc: dict, blocks: dict[str, str]) -> list[Issue]:
    """
    模型输出可能结构不合规（schema_invalid 已记录）；后续检查遇到异常结构时不得崩溃，记为 validator_error
    """
    issues = schema_issues(doc)
    if any(i.rule_code == 'schema_invalid' for i in issues) and not isinstance(doc.get('findings'), list):
        return issues
    for check in (
        ref_issues,
        lambda d: quote_issues(d, blocks),
        lambda d: numeric_issues(d, blocks),
        population_issues,
        concept_issues,
        completeness_issues,
    ):
        try:
            issues += check(doc)
        except (TypeError, AttributeError, KeyError, ValueError) as e:  # noqa: PERF203  每项检查独立容错
            issues.append(Issue('validator_error', 'error', 'document', None, f'结构异常，无法完成检查: {e}'))
    return issues
