<template>
  <div class="kg">
    <div class="kg-bar">
      <span class="muted">{{ graph.nodes.length }} 个节点 · {{ graph.links.length }} 条关系 · {{ quoteCount }} 处原文引用</span>
      <span class="muted hint">点击节点或连线查看抽取内容与原文；滚轮缩放，拖动节点调整布局；点图例可隐藏某类节点</span>
      <el-button size="small" text @click="reset"><el-icon><Refresh /></el-icon>重新布局</el-button>
    </div>
    <div class="kg-body">
      <div ref="el" class="kg-canvas" />
      <aside class="kg-panel">
        <template v-if="sel">
          <div class="p-type"><i :style="{ background: sel.color }" />{{ sel.kind }}</div>
          <h3 class="p-title">
            <router-link v-if="sel.link" :to="sel.link">{{ sel.title }}</router-link><span v-else>{{ sel.title }}</span>
          </h3>
          <div v-if="sel.edge" class="p-edge">
            <a href="#" @click.prevent="select(sel.edge.source)">{{ nodeName(sel.edge.source) }}</a>
            <span class="rel">{{ sel.edge.label }}</span>
            <a href="#" @click.prevent="select(sel.edge.target)">{{ nodeName(sel.edge.target) }}</a>
          </div>
          <dl class="p-fields" v-if="sel.fields?.length">
            <template v-for="(f, i) in sel.fields" :key="i"><dt>{{ f[0] }}</dt><dd>{{ f[1] }}</dd></template>
          </dl>
          <div v-if="sel.spans?.length" class="p-sec">
            <div class="p-sec-title">原文引用{{ sel.spansFrom ? `（${sel.spansFrom}）` : "" }}</div>
            <div v-for="s in sel.spans" :key="s.spanId + (s.role || '')" class="quote">
              {{ s.quote }}
              <div class="meta">
                <span>第 {{ s.page ?? "?" }} 页</span>
                <el-tag v-if="!s.quoteVerified" size="small" type="warning" effect="plain">未精确定位</el-tag>
                <router-link v-if="s.blockId" :to="{ path: `/record/${recordId}`, query: { tab: 'fulltext', block: s.blockId } }">在全文中查看 ›</router-link>
              </div>
            </div>
          </div>
          <div v-else class="muted p-sec">该条目没有单独的原文引用。</div>
          <div v-if="sel.neighbors?.length" class="p-sec">
            <div class="p-sec-title">关联（{{ sel.neighbors.length }}）</div>
            <div v-for="(n, i) in sel.neighbors" :key="i" class="nb" @click="select(n.id)">
              <span class="nb-rel">{{ n.dir === "out" ? n.label + " →" : "← " + n.label }}</span>
              <i :style="{ background: n.color }" /><span class="nb-name">{{ n.name }}</span>
            </div>
          </div>
        </template>
        <div v-else class="empty muted">
          <el-icon :size="28"><Share /></el-icon>
          <p>从原文抽取出的研究、人群、组别、结局、研究发现与医学概念，以及它们之间的关系。</p>
          <p>选中任一节点或连线，这里会显示抽取字段和对应的原文句子。</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import echarts from "@/chartTheme";
import { ARM_ROLE, CONCEPT_TYPE, DESIGN_COARSE, FINDING_TYPE, MEASURE, POP_COND_KIND, POP_ROLE, PREDICATE, PRIMACY, REL_TYPE, STRENGTH, ACTION_TYPE, fmtNum, fmtRange, label, relSubjectIsParent } from "@/labels";

const props = defineProps({ kb: Object, recordId: [String, Number], focus: String });
const CATS = [
  { name: "研究", color: "#0e434c", size: 46 }, { name: "人群", color: "#7486a8", size: 30 }, { name: "组别", color: "#4c97a1", size: 30 },
  { name: "结局", color: "#c77d34", size: 24 }, { name: "研究发现", color: "#1d6f7a", size: 16 }, { name: "推荐意见", color: "#2f8a5b", size: 20 },
  { name: "概念", color: "#b5473a", size: 22 }, { name: "未归一术语", color: "#b9b2a6", size: 16 },
];
const CI = Object.fromEntries(CATS.map((c, i) => [c.name, i]));
const short = (s, n = 16) => (!s ? "" : s.length > n ? s.slice(0, n) + "…" : s);

// 把 record 详情里的抽取结果拼成节点 + 连线；每个节点/连线带上详情字段和原文片段
const graph = computed(() => {
  const kb = props.kb || {};
  const nodes = new Map(); const links = [];
  const node = (id, cat, name, info) => {
    if (!nodes.has(id)) nodes.set(id, { id, name: short(name), category: CI[cat], info: { kind: cat, title: name, color: CATS[CI[cat]].color, ...info } });
    return id;
  };
  const concept = (cid) => {
    const c = kb.concepts?.[cid];
    if (!c) return null;
    const name = c.nameZh || c.nameEn;
    return node(`c:${cid}`, "概念", name, {
      kind: `概念 · ${label(CONCEPT_TYPE, c.conceptType)}`, link: `/concept/${cid}`,
      fields: [["中文名", c.nameZh || "-"], ["英文名", c.nameEn || "-"], ["类型", label(CONCEPT_TYPE, c.conceptType)]],
    });
  };
  const term = (text) => node(`t:${text}`, "未归一术语", text, { fields: [["说明", "原文表述未映射到标准概念"]] });
  const link = (source, target, lbl, spans, fields) => { if (source && target && source !== target) links.push({ source, target, value: lbl, label: lbl, spans: spans || [], fields }); };

  for (const s of kb.studies || []) {
    node(`s:${s.studyId}`, "研究", s.studyName || label(DESIGN_COARSE, s.designCoarse), {
      spans: s.spans, fields: [["设计", label(DESIGN_COARSE, s.designCoarse)], ["原文设计", s.designAsReported || "-"], ["国家/地区", (s.countries || []).join("、") || "-"],
        ["研究期间", s.periodAsReported || "-"], ["中心数", s.nCenters || "-"], ["注册号", (s.registrationIds || []).join("、") || "-"]],
    });
  }
  for (const p of kb.populations || []) {
    const id = node(`p:${p.populationId}`, "人群", p.label || label(POP_ROLE, p.role), {
      spans: p.spans, fields: [["角色", label(POP_ROLE, p.role)], ["样本量", p.nValue ? fmtNum(p.nValue) : "-"], ["年龄", fmtRange(p.ageLow, p.ageHigh, p.ageUnit) || "-"], ["场景", p.indicationSetting || "-"]],
    });
    if (p.parentPopulationId) link(`p:${p.parentPopulationId}`, id, "亚组");
    else link(`s:${p.studyId}`, id, "研究人群");
    for (const c of p.conditions) {
      const t = c.conceptId ? concept(c.conceptId) : c.valueAsReported ? term(c.valueAsReported) : null;
      link(id, t, label(POP_COND_KIND, c.kind), c.spans, [["条件", label(POP_COND_KIND, c.kind)], ["原文取值", c.valueAsReported || "-"]]);
    }
  }
  for (const a of kb.arms || []) {
    const id = node(`a:${a.armId}`, "组别", a.label, { fields: [["角色", label(ARM_ROLE, a.role)]] });
    link(`s:${a.studyId}`, id, "分组");
    for (const x of a.interventions) {
      const detail = [x.doseAsReported || fmtRange(x.doseLow, x.doseHigh, x.doseUnit), x.frequencyAsReported, x.route, fmtRange(x.durationLow, x.durationHigh, x.durationUnit)].filter(Boolean).join(" · ");
      link(id, x.conceptId ? concept(x.conceptId) : null, "干预/暴露", x.spans, [["剂量/方式", detail || "-"], ["补充", x.detailsAsReported || "-"]]);
    }
  }
  for (const o of kb.outcomes || []) {
    const id = node(`o:${o.outcomeId}`, "结局", o.labelAsReported || o.concept, {
      spans: o.spans, fields: [["主次", label(PRIMACY, o.primacy)], ["时间点", fmtRange(o.timepointLow, o.timepointHigh, o.timepointUnit) || "-"], ["判定方式", o.ascertainment || "-"]],
    });
    link(`s:${o.studyId}`, id, "结局指标");
    if (o.conceptId) link(id, concept(o.conceptId), "对应概念");
  }
  (kb.findings || []).forEach((f, i) => {
    const res = (f.results || []).map((r) => `${label(MEASURE, r.measure)}${r.category || r.arm ? `（${[r.arm, r.category].filter(Boolean).join("·")}）` : ""}：${r.valueAsReported || r.value}`);
    const title = `${f.exposureAsReported || f.exposureConcept || f.exposureArm || ""}${f.comparatorAsReported ? ` vs ${f.comparatorAsReported}` : ""} → ${f.outcomeLabel || f.outcomeConcept || ""}`;
    const id = node(`f:${f.findingId}`, "研究发现", `F${i + 1}`, {
      title: `F${i + 1} ${title}`, spans: f.spans,
      fields: [["类型", label(FINDING_TYPE, f.findingType)], ["模型", f.modelType || "-"], ...res.slice(0, 8).map((r, j) => [j ? "" : "结果", r])],
    });
    const end = (armId, cid, text) => (armId && nodes.has(`a:${armId}`) ? `a:${armId}` : cid ? concept(cid) : text ? term(text) : null);
    link(id, end(f.exposureArmId, f.exposureConceptId, f.exposureAsReported), "暴露");
    link(id, end(f.comparatorArmId, f.comparatorConceptId, f.comparatorAsReported), "对照");
    link(id, f.outcomeId && nodes.has(`o:${f.outcomeId}`) ? `o:${f.outcomeId}` : f.outcomeConceptId ? concept(f.outcomeConceptId) : null, "结局");
    if (f.populationId && nodes.has(`p:${f.populationId}`)) link(id, `p:${f.populationId}`, "人群");
  });
  for (const a of kb.assertions || []) link(concept(a.subjectConceptId), concept(a.objectConceptId), label(PREDICATE, a.predicate), a.spans, [["语境", a.context || "-"]]);
  for (const d of kb.definitions || []) {
    const id = concept(d.conceptId);
    if (id) { const n = nodes.get(id).info; n.spans = [...(n.spans || []), ...d.spans]; n.spansFrom = "术语定义"; }
  }
  // 连线方向按句子读法：is_a 由子指向父（子 属于 父），has_symptom 由疾病指向症状（疾病 表现为 症状）
  for (const r of kb.conceptRelations || []) {
    const [from, to] = relSubjectIsParent(r.relType) ? [r.parentId, r.childId] : [r.childId, r.parentId];
    link(concept(from), concept(to), label(REL_TYPE, r.relType), r.spans);
  }
  for (const r of kb.recommendations || []) {
    const id = node(`r:${r.recId}`, "推荐意见", r.numberAsReported || `R${r.recId}`, {
      title: r.textVerbatim, spans: r.spans, fields: [["强度", label(STRENGTH, r.strength)], ["原文分级", [r.strengthAsReported, r.certaintyAsReported].filter(Boolean).join("，") || "-"]],
    });
    for (const c of r.conditions) link(id, c.conceptId ? concept(c.conceptId) : null, "适用条件", [], [["取值", c.valueAsReported || "-"]]);
    for (const a of r.actions) link(id, a.targetConceptId ? concept(a.targetConceptId) : null, label(ACTION_TYPE, a.actionType), [], [["原文", a.textAsReported || "-"]]);
  }
  // 节点大小随连接数增长
  const deg = {};
  for (const l of links) { deg[l.source] = (deg[l.source] || 0) + 1; deg[l.target] = (deg[l.target] || 0) + 1; }
  const list = [...nodes.values()].map((n) => ({ ...n, symbolSize: CATS[n.category].size + Math.min(18, (deg[n.id] || 0) * 1.5) }));
  return { nodes: list, links, byId: Object.fromEntries(list.map((n) => [n.id, n])) };
});
const quoteCount = computed(() => graph.value.nodes.reduce((a, n) => a + (n.info.spans?.length || 0), 0) + graph.value.links.reduce((a, l) => a + l.spans.length, 0));

const sel = ref(null);
const nodeName = (id) => graph.value.byId[id]?.info.title || id;
function neighbors(id) {
  return graph.value.links.filter((l) => l.source === id || l.target === id).map((l) => {
    const other = graph.value.byId[l.source === id ? l.target : l.source];
    return { id: other.id, name: other.info.title, color: other.info.color, label: l.label, dir: l.source === id ? "out" : "in" };
  });
}
function select(id) {
  const n = graph.value.byId[id];
  if (!n) return;
  sel.value = { ...n.info, neighbors: neighbors(id) };
  chart?.dispatchAction({ type: "downplay" });
  chart?.dispatchAction({ type: "highlight", seriesIndex: 0, name: n.name, dataIndex: graph.value.nodes.indexOf(n) });
}
function selectEdge(l) {
  const src = graph.value.byId[l.source];
  // 连线自身没有原文时，退回到发出方条目的原文（如研究发现 → 暴露）
  const own = l.spans?.length;
  sel.value = { kind: "关系", color: "#83939c", title: l.label, edge: l, fields: l.fields, spans: own ? l.spans : src?.info.spans, spansFrom: own ? "" : src ? `来自「${short(src.info.title, 20)}」` : "" };
}

const el = ref(); let chart = null;
const onResize = () => chart?.resize();
function render() {
  if (!el.value) return;
  chart = chart || echarts.init(el.value, "kb");
  const g = graph.value;
  chart.setOption({
    tooltip: { formatter: (p) => (p.dataType === "edge" ? `${nodeName(p.data.source)}<br/>— ${p.data.label} →<br/>${nodeName(p.data.target)}` : `<b>${p.data.info.kind}</b><br/>${short(p.data.info.title, 60)}`) },
    legend: [{ data: CATS.map((c) => c.name), top: 6, left: 10, itemGap: 12, textStyle: { fontSize: 12 } }],
    animationDurationUpdate: 300,
    series: [{
      type: "graph", layout: "force", roam: true, draggable: true, top: 40,
      categories: CATS.map((c) => ({ name: c.name, itemStyle: { color: c.color } })),
      data: g.nodes, links: g.links,
      force: { repulsion: 260, edgeLength: [50, 140], gravity: 0.08, friction: 0.2 },
      label: { show: true, position: "right", fontSize: 11, color: "#33444c" },
      labelLayout: { hideOverlap: true },
      lineStyle: { color: "#b9c6ca", width: 1.2, curveness: 0.08, opacity: 0.9 },
      edgeSymbol: ["none", "arrow"], edgeSymbolSize: 6,
      edgeLabel: { show: false, formatter: (p) => p.data.label, fontSize: 10, color: "#7a4614" },
      emphasis: { focus: "adjacency", lineStyle: { width: 2.4, color: "#c77d34" }, edgeLabel: { show: true } },
      itemStyle: { borderColor: "#fff", borderWidth: 1.5 },
    }],
  }, true);
  chart.off("click");
  chart.on("click", (p) => (p.dataType === "edge" ? selectEdge(p.data) : select(p.data.id)));
}
function reset() { sel.value = null; chart?.clear(); render(); }
onMounted(() => { render(); window.addEventListener("resize", onResize); if (props.focus) select(props.focus); });
onBeforeUnmount(() => { window.removeEventListener("resize", onResize); chart?.dispose(); chart = null; });
watch(() => props.kb, render);
</script>

<style scoped>
.kg-bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
.kg-bar .hint { margin-left: auto; font-size: 12px; }
.kg-body { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 12px; }
.kg-canvas { height: 680px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: radial-gradient(circle at 50% 40%, #fbfdfd, #f3f7f7); }
.kg-panel { height: 680px; overflow: auto; border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 14px; background: #fff; }
.empty { text-align: center; padding-top: 120px; line-height: 1.8; }
.p-type { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-text-3); font-weight: 600; letter-spacing: 0.5px; }
.p-type i, .nb i { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.p-title { font-size: 15px; line-height: 1.55; margin: 6px 0 10px; font-family: var(--font-serif); }
.p-title a { color: var(--c-text); }
.p-edge { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 13px; margin-bottom: 10px; }
.p-edge .rel { background: var(--c-accent-50); color: #7a4614; border-radius: 4px; padding: 0 8px; }
.p-fields { display: grid; grid-template-columns: 72px 1fr; gap: 4px 8px; margin: 0 0 8px; font-size: 12.5px; }
.p-fields dt { color: var(--c-text-3); }
.p-fields dd { margin: 0; color: var(--c-text); word-break: break-word; }
.p-sec { margin-top: 12px; }
.p-sec-title { font-size: 12.5px; font-weight: 600; color: var(--c-text-2); margin-bottom: 4px; }
.nb { display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 4px; font-size: 12.5px; cursor: pointer; }
.nb:hover { background: var(--c-primary-50); }
.nb-rel { color: var(--c-text-3); flex-shrink: 0; min-width: 70px; }
.nb-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--c-text); }
@media (max-width: 1100px) { .kg-body { grid-template-columns: minmax(0, 1fr); } .kg-panel { height: auto; } }
</style>
