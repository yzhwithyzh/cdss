// 枚举值的中文标签（取值来自 kb_* 抽取 schema 与 lit_study_design）
export const CONCEPT_TYPE = {
  disease_or_lesion: "疾病/病变", test_or_index: "检查/指标", outcome_event: "结局事件", procedure: "操作/术式",
  biomarker: "生物标志物", drug: "药物", exposure_factor: "暴露因素", population_trait: "人群特征",
  symptom_or_sign: "症状/体征", gene_or_pathway: "基因/通路", quality_metric: "质量指标", histology: "组织学",
  morphology: "形态学", genetic_syndrome: "遗传综合征", anatomic_site: "解剖部位", organism: "微生物", other: "其他",
};
export const CONCEPT_COLOR = {
  disease_or_lesion: "#b5473a", test_or_index: "#1d6f7a", outcome_event: "#c77d34", procedure: "#4c97a1",
  biomarker: "#8a6fa3", drug: "#6d9a7b", exposure_factor: "#9a7b55", population_trait: "#7486a8",
  symptom_or_sign: "#d6aa6c", gene_or_pathway: "#5f7f4a", quality_metric: "#2f8a8a", histology: "#a0607a",
  morphology: "#7a6a5a", genetic_syndrome: "#5a6aa0", anatomic_site: "#6a8a9a", organism: "#8a9a5a", other: "#93a1aa",
};

export const DOC_KIND = {
  original_research: "原始研究", narrative_review: "叙述性综述", systematic_review: "系统评价", case_report_or_series: "病例报告/系列",
  methodology_or_technical: "方法学/技术", guideline: "指南/共识", other: "其他",
};
export const SUBJECT_SCOPE = { human: "人体研究", animal: "动物研究", in_silico: "计算研究", in_vitro: "体外研究", mixed: "混合" };

export const DESIGN_COARSE = {
  rct: "随机对照试验", cohort: "队列研究", case_control: "病例对照研究", cross_sectional: "横断面研究",
  sr_ma: "系统评价/Meta分析", case_series: "病例系列", case_report: "病例报告", diagnostic_accuracy: "诊断准确性研究", other: "其他",
};

export const FINDING_TYPE = {
  association: "关联分析", comparative_effect: "比较效应", descriptive_frequency: "描述性频率", diagnostic_accuracy: "诊断准确性",
  pooled_estimate: "合并估计", descriptive_continuous: "描述性连续变量", time_to_event: "生存/时间事件", correlation: "相关分析",
  case_observation: "个案观察", other: "其他",
};
export const STATEMENT_ROLE = { own_result: "本研究结果", cited_result: "引用他人结果", author_conclusion: "作者结论" };

export const MEASURE = {
  or: "OR", hr: "HR", rr: "RR", irr: "IRR", rd: "风险差", md: "均数差", smd: "标准化均数差", proportion: "比例", count: "计数",
  mean: "均值", median: "中位数", n_over_N: "n/N", sensitivity: "敏感度", specificity: "特异度", ppv: "阳性预测值",
  npv: "阴性预测值", accuracy: "准确度", auc: "AUC", correlation: "相关系数", other: "其他",
};
// 比值类指标：森林图用对数轴，无效线在 1
export const RATIO_MEASURES = new Set(["or", "hr", "rr", "irr"]);

export const STRENGTH = { strong: "强推荐", weak: "弱推荐", other: "其他", not_reported: "未报告" };
export const STRENGTH_TAG = { strong: "success", weak: "warning", other: "info", not_reported: "info" };
export const STRENGTH_COLOR = { strong: "#2f8a5b", weak: "#d49a3a", other: "#7486a8", not_reported: "#c3ccd2" };
export const CERTAINTY = { high: "高", moderate: "中", low: "低", very_low: "极低", other: "其他", not_reported: "未报告" };

export const ACTION_TYPE = {
  surveillance_exam: "随访检查", resection_technique: "切除技术", test: "检查/检测", biopsy: "活检", drug_start: "开始用药",
  drug_avoid: "避免用药", quality_target: "质量目标", referral: "转诊", training: "培训", other: "其他",
};
export const POP_COND_KIND = { inclusion: "纳入", exclusion: "排除", subgroup_definition: "亚组定义", risk_background: "风险背景" };
export const POP_ROLE = {
  overall: "总体", subgroup: "亚组", arm: "组别", case_group: "病例组", control_group: "对照组", analysis_set: "分析集", guideline_scope: "指南适用人群",
};
export const ARM_ROLE = { intervention: "干预组", control: "对照组", exposure_level: "暴露水平", reference_level: "参照水平" };
export const PRIMACY = { primary: "主要结局", secondary: "次要结局", not_stated: "未声明" };
export const PREDICATE = {
  increases: "增加", decreases: "降低", associated_with: "相关", no_significant_difference: "无显著差异",
  expressed_in: "表达于", subtype_of: "亚型", located_in: "位于", used_for: "用于", other: "其他",
};
export const REL_TYPE = { is_a: "属于", has_symptom: "表现为" };
// 关系表 parent→child 的读法：is_a 是“子 属于 父”，has_symptom 是“疾病（父）表现为 症状（子）”
const REL_SUBJECT_IS_PARENT = { is_a: false, has_symptom: true };
// 接口里 dir='parent' 表示对方是父端；返回对方在句子里是不是主语
export const relOtherIsSubject = (relType, dir) => (dir === "parent") === (REL_SUBJECT_IS_PARENT[relType] ?? true);
export const relSubjectIsParent = (relType) => REL_SUBJECT_IS_PARENT[relType] ?? true;
export const LANG = { en: "英文", zh: "中文", ja: "日文", de: "德文", fr: "法文", unknown: "未知" };
export const BLOCK_TYPE = { paragraph: "段落", table: "表格", caption: "图注", list_item: "列表项", heading: "标题" };
export const MATCH_STATUS = { candidate: "候选匹配", confirmed: "已确认", unmatched: "未匹配" };
export const SPAN_ENTITY = {
  finding: "研究发现", population: "人群", population_condition: "纳排条件", assertion: "断言", study: "研究信息",
  recommendation: "推荐意见", definition: "术语定义", outcome: "结局定义", arm_intervention: "干预", concept_relation: "概念关系",
};

export const label = (map, k) => (k == null ? "-" : map[k] ?? k);

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// 大小写不敏感高亮，先转义再包 <mark>
export function highlight(text, q) {
  if (!text) return "";
  if (!q) return esc(text);
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  let out = "", last = 0;
  for (const m of String(text).matchAll(re)) {
    out += esc(text.slice(last, m.index)) + `<mark>${esc(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

export const fmtNum = (n) => (n == null ? "–" : Number(n).toLocaleString("zh-CN"));

// 区间 [low, high) 的人话写法，单位可选
export function fmtRange(low, high, unit) {
  const u = unit ? ` ${unit}` : "";
  if (low != null && high != null) return Number(low) === Number(high) ? `${low}${u}` : `${low}–${high}${u}`;
  if (low != null) return `≥ ${low}${u}`;
  if (high != null) return `< ${high}${u}`;
  return "";
}

export function fmtEffect(r) {
  if (r.value == null) return r.valueAsReported || "-";
  const v = Number(r.value);
  const ci = r.ciLow != null && r.ciHigh != null ? ` (${Number(r.ciLow)}–${Number(r.ciHigh)})` : "";
  return `${v}${ci}`;
}

export const conceptLink = (id) => (id ? `/concept/${id}` : null);
