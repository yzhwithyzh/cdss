"""
抽取提示词：系统规则 + JSON Schema + 文献解析块
"""

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name('schema') / 'extraction.schema.json'
PROMPT_VERSION = 'extract-v0.8'

SYSTEM_RULES = """你是医学文献结构化抽取助手。只输出一个 JSON 对象，必须符合给定的 JSON Schema，不得输出任何其他文字。

【原文定位】
1. 每个研究结果、人群、定义都要引用原文：先在 evidence_spans 中登记片段（span_id 用 S1、S2…），
   block_id 必须是下方原文块方括号中的块 ID，quote 必须从该块中逐字复制（可截取其中连续的一段，不得改写、翻译、补字）。
2. 表格中的数值引用表格块，quote 复制含该数值的那一行。

【数值】
3. 只抽原文明确报告的数值；未报告的填 null。不得推算、换算、补全，
   value_as_reported 必须是该结果 source_span_ids 中某个 quote 里**连续、逐字**的一段：
   不得添加标签或说明（如“Male 2010:”“本文算法”），不得把几处数值拼成一句，不得改写单位或符号；
   一个数值来自几处原文，就分别登记几个片段。程序会逐字核对，对不上的会退回修改。
   例：原文“OR, 0.83 (95% CI 0.52–1.33)”→ value_as_reported 写“0.83 (95% CI 0.52–1.33)”。
   不得由比例反算人数，不得自行计算差值或置信区间。
   numerator / denominator 只能取原文明确写出的数字：引用片段中的数字，或原文明确报告的该组样本量
   （如表头“UCSP (n = 122)”、正文“122 polyps in the UCSP group”）；不得由百分比反推（如“13 cases (26%)”不得推出分母 50）。
   分母取自表头或正文样本量时，尽量把该处也登记为片段并加入 source_span_ids；原文没有分母时填 null。
4. 原文数值看起来有错（如 SE 异常、β 与 OR 方向矛盾）也照原文抽取，不要纠正。

【完整性】
4a. 本研究的结果表（基线特征表除外）要逐行抽取：每个因素/每个结局一行一个 finding，
    **无统计学意义的结果（P>0.05、可信区间跨 1）同样必须抽取**，不得只挑显著结果。
    同一张表的单因素与多因素结果分别成为 model.type=crude 与 adjusted 的 finding。
4b. 基线特征表只抽总样本量和分组人数，不逐行抽取。
4c. 正文中报告、表格中没有的本研究结果也要抽取。
4d. 粒度：每个 finding 只对应一个暴露/因素（或一组比较）、一个结局、一个人群、一个模型。
    不同的因素（如性别、年龄、吸烟）、不同的结局、不同的亚组人群必须拆成不同的 finding。
    同一变量的不同类别/水平/时点（如症状构成“腹痛/呕吐”、部位“近端/远端”、剂量“≥30/4–29 片/月”、时点“1年/3年”）
    可以放在同一个 finding，但每个 result 必须用 category 写明对应的类别（原文用词）。
    各组的数值用 arm_ref 区分。没有 arm_ref 也没有 category 的同一种 result 只能出现一次。
    P 值写在对应结果的 p_as_reported 中，不单独作为一个 result。
4e. 每个 finding 都必须填写 outcome_ref，包括 cited_result 和 author_conclusion：先在 outcomes 中登记该结局（原文用词），
    再引用；描述性频率指向被计数的事件或特征。只有 finding_type=case_observation 可以为 null。

【陈述角色】
5. statement_role=own_result 只取本研究在“结果”部分或表格中报告的数值；摘要里的数值可用于定位，
   但应优先引用正文或表格中的同一结果。讨论或引言中引用他人研究的结果标 cited_result；
   作者的结论性表述标 author_conclusion。
6. 综述、叙述性文章不产生 own_result；其中的数字都是 cited_result。基础研究（动物、细胞）的结果放 assertions，不放 findings。

【研究设计与人群】
7. design_coarse 只在原文明确或可从方法确定时选择，不确定时 design_uncertainty=high。
8. 人群年龄只填纳入标准或推荐适用范围中的界限（age.basis），样本的均值/中位数不填。
8a. 纳入/排除标准中可判定的病变或患者条件（病灶大小、数目、组织学、形态分型、部位、检查场景、既往史、用药等）
    必须逐条写入 eligibility_conditions / exclusion_conditions（concept + quantity），不能只写在人群 label 里；
    label 中出现的数值条件（如“5-10 mm”“≥50岁”）也必须有对应的结构化条件或 age 字段。
9. 亚组必须作为 role=subgroup 的人群，并用 subgroup_conditions 写出结构化定义；有总体人群时 parent_population_ref 指向它
   （没有总体人群时可为 null），亚组结果的 parent_finding_ref 指向总体结果。
   比较性结果按亚组分别报告时（亚组分析，如按息肉大小 5-7 mm / 8-10 mm 分别比较两组），必须为每个亚组建立人群，
   每个亚组的比较各成一个 finding；只有描述性的构成比（如部位分布、症状构成）才用 category 区分类别。

【定义与概念】
11. 概念只填原文用词与类型；concept_code、rule_field_code 只在下方给出的对照表中有完全对应项时填写，否则填 null。
    不得自创编码。definition_variant_id 一律填 null；结局的原文定义句通过 definition_span_ids 引用。
11a. 概念 text 只写概念本身的名称（原文用词），一个 text 只放一个概念：
    - 不得带数值、比较符或限定条件：“腺瘤直径≥1 cm”→ concept=腺瘤 + quantity（≥1 cm）；
      “obesity (BMI ≥30.0 kg/m2)”→ concept=obesity，BMI≥30 写入 quantity 或 level；
      “ADR less than 20%”→ concept=adenoma detection rate + quantity；
      “risk of colorectal cancer”→ concept=colorectal cancer（风险由 measure 表达）。
    - 多个概念并列（“CEA, CA19-9, CA125”“9D, 3D, and 8D”）必须拆开：分别登记，或拆成多个 finding/条件。
    程序会检查 text 中的数值、比较符和逗号并列，不符合的会退回修改。
11b. studies.registration_ids：原文写明试验注册号（如 NCT01234567、ChiCTR…、ISRCTN…）时必须照录。
12. 只抽取文中方法段或指南定义条款给出的操作性定义；背景中的泛泛描述不算定义。
12a. 复发、再发、新发腺瘤类结局必须填写 outcome_scope：原切除部位复发=local_recurrence；
     其他部位新发=metachronous；原文定义同时包含两者（如“原位或异位发现腺瘤”）=mixed；原文未说明=unclear。
     不属于此类的结局填 not_applicable。
12b. 疾病或病变的临床表现：原文明确陈述某疾病/病变可表现为某症状或体征（如“结直肠癌可表现为便血、排便习惯改变”）时，
     写入 assertions：subject=疾病/病变（concept_type=disease_or_lesion），predicate=has_symptom，
     object=症状/体征（concept_type=symptom_or_sign），span_ids 指向该陈述。
     不得从研究人群的纳入条件、就诊指征、不良反应或症状构成比推断这种关系。
12c. studies.terminology_version：原文写明病理或内镜分类所依据的标准及版本（如 WHO 2019、WHO 2010）时照录，
     未写明填 null，不得按发表年份推断。

【范围】
13. 指南/共识（doc_kind=guideline 或 consensus_or_position）逐条填写 recommendations，并填写 guideline_info：
    制定机构、适用地区、版本、发布日期、被本指南替代的旧版本，均照录原文，未写明的填 null；其他文献 guideline_info 填 null。
14. 无法用当前结构表达但重要的内容写入 schema_extension_candidates，不得新增字段。
15. 文献与结直肠腺瘤/息肉无关、或不是研究性文献（如学位论文目录页）时，document 中如实分类，findings 可为空。"""

DICTIONARIES = {
    'definition_variants': [
        {
            'id': 'AA-v1',
            'concept': 'advanced adenoma',
            'definition': '≥10 mm，或含绒毛成分（任意比例），或高级别异型增生',
        },
        {'id': 'AA-v2', 'concept': 'advanced adenoma', 'definition': '≥10 mm，或绒毛成分 ≥25%，或高级别异型增生'},
        {
            'id': 'AA-v3',
            'concept': 'advanced adenoma',
            'definition': '>10 mm（严格大于），或绒毛成分，或高级别异型增生',
        },
        {
            'id': 'AA-v4',
            'concept': 'advanced adenoma',
            'definition': '仅按组织学（绒毛成分或高级别异型增生），不含大小',
        },
        {'id': 'AN-v1', 'concept': 'advanced neoplasia', 'definition': '进展期腺瘤或浸润癌'},
    ],
    'rule_input_fields': [
        {'code': 'adenoma_count', 'desc': '本次检查腺瘤数目'},
        {'code': 'largest_size_mm', 'desc': '最大病灶直径（mm）'},
        {'code': 'histology_flags', 'desc': '组织学：管状/管状绒毛/绒毛/高级别异型增生/黏膜内癌'},
        {'code': 'ssl_count', 'desc': '无蒂锯齿状病变数目'},
        {'code': 'ssl_max_size_mm', 'desc': '最大锯齿状病变直径'},
        {'code': 'ssl_dysplasia', 'desc': '锯齿状病变伴异型增生'},
        {'code': 'piecemeal_ge20mm', 'desc': '≥20 mm 分片切除'},
        {'code': 'bowel_prep_adequate', 'desc': '肠道准备充分'},
        {'code': 'cecal_intubation', 'desc': '盲肠插管'},
        {'code': 'family_history', 'desc': '结直肠癌家族史'},
        {'code': 'age', 'desc': '年龄'},
    ],
}


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))


def compact_schema() -> str:
    """
    去掉 $schema/$id 等元信息、压缩空白，减少 token
    """
    schema = load_schema()
    for key in ('$schema', '$id'):
        schema.pop(key, None)
    return json.dumps(schema, ensure_ascii=False, separators=(',', ':'))


def system_prompt() -> str:
    return (
        f'{SYSTEM_RULES}\n\n【对照表】\n'
        f'{json.dumps({"rule_input_fields": DICTIONARIES["rule_input_fields"]}, ensure_ascii=False)}\n\n'
        f'【JSON Schema】\n{compact_schema()}'
    )


def format_blocks(blocks: list[dict]) -> str:
    """
    原文块 → 带块 ID 的纯文本：[块ID|章节|类型|页码] 文本
    """
    lines = []
    for b in blocks:
        page = f'p{b["page"]}' if b.get('page') else '-'
        lines.append(f'[{b["block_key"]}|{b["section"]}|{b["block_type"]}|{page}] {b["text"]}')
    return '\n'.join(lines)


def user_prompt(document_version_id: str, meta: dict, blocks: list[dict]) -> str:
    head = {
        'document_version_id': document_version_id,
        'title': meta.get('title'),
        'year': meta.get('year'),
        'study_design_label_from_screening': meta.get('design_label'),
    }
    return (
        f'文献信息：{json.dumps(head, ensure_ascii=False)}\n'
        '（筛选阶段的研究类型标签可能不准，以原文为准。）\n\n'
        f'原文块：\n{format_blocks(blocks)}\n\n'
        f'请按规则输出 JSON，document_version_id 填 "{document_version_id}"，schema_version 填 "kb-extract-0.1"。'
    )


# ---------------- 分段抽取（输出超长时的回退方案）----------------
SKELETON_NOTE = (
    '\n\n【本次为分段抽取第 1 步：骨架】输出完整 JSON，但 findings 填空数组 []；'
    'document、studies、populations、arms、outcomes、definitions、recommendations、guideline_info、meta_analysis、'
    'assertions 照常完整填写。'
    '研究结果将在后续步骤按原文分段抽取，并引用这里定义的 study_ref / population_ref / arm_ref / outcome_ref。'
)


def chunk_prompt(skeleton: dict, chunk_blocks: list[dict], index: int, total: int) -> str:
    """
    分段抽取第 2 步：给定骨架与一段原文块，只抽这一段中的研究结果

    编号区间按段区分（第 i 段：片段 S{i}001 起、结果 F{i}001 起、新增人群/组别/结局 P{i}01/A{i}01/O{i}01 起），
    合并时无需重新编号
    """
    frame = {k: skeleton.get(k) for k in ('studies', 'populations', 'arms', 'outcomes')}
    return (
        f'已抽取的骨架（研究、人群、组别、结局）：\n{json.dumps(frame, ensure_ascii=False, separators=(",", ":"))}\n\n'
        f'下面是原文第 {index}/{total} 段。只抽取这一段原文块中的研究结果（findings），'
        f'并登记它们引用的原文片段（evidence_spans）。规则与前述完全相同，其中：\n'
        f'- quote 只能逐字摘自本段的原文块；新登记的 span_id 从 S{index}001 开始编号；\n'
        f'- finding_ref 从 F{index}001 开始编号；\n'
        f'- study_ref / population_ref / arm_ref / outcome_ref 优先引用骨架中已有的；确需新增时，'
        f'人群、组别、结局分别从 P{index}01、A{index}01、O{index}01 编号，并放进对应数组；\n'
        f'- parent_finding_ref 只能引用本段内的 finding；\n'
        f'- 本段没有研究结果时，各数组为空。\n'
        '输出一个 JSON 对象，只包含 evidence_spans、findings、populations、arms、outcomes '
        '（后三者只放新增项）。\n\n'
        f'本段原文块：\n{format_blocks(chunk_blocks)}'
    )


CHUNK_KEYS = ('evidence_spans', 'findings', 'populations', 'arms', 'outcomes')


def merge_chunk(base: dict, part: dict) -> dict:
    """
    把一段的抽取结果并入骨架（列表追加，不重新编号）
    """
    merged = json.loads(json.dumps(base))
    for key in CHUNK_KEYS:
        items = part.get(key) or []
        if isinstance(items, list):
            merged[key] = list(merged.get(key) or []) + items
    return merged
