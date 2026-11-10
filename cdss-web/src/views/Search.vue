<template>
  <div class="page">
    <div class="scope-bar card">
      <el-radio-group v-model="f.scope" @change="onScope">
        <el-radio-button v-for="(v, k) in SCOPES" :key="k" :value="k">{{ v }}</el-radio-button>
      </el-radio-group>
      <div class="cond">
        <el-tag v-if="f.q" closable @close="setF('q', '')">关键词：{{ f.q }}</el-tag>
        <el-tag v-for="t in activeTags" :key="t.k" closable type="info" @close="setF(t.k, null)">{{ t.text }}</el-tag>
        <span class="muted" v-if="!f.q && !activeTags.length">未设置条件</span>
      </div>
      <span class="muted total">共 <span class="count-strong">{{ fmtNum(total) }}</span> 条</span>
    </div>

    <div class="layout-3" style="margin-top: 16px">
      <aside class="card sticky">
        <template v-if="f.scope === 'record'">
          <div class="facet-title">筛选</div>
          <div class="facet-item" :class="{ active: f.structured }" @click="setF('structured', !f.structured)"><span><el-icon><Finished /></el-icon> 仅深度结构化</span><span class="muted">{{ fmtNum(facets.structured) }}</span></div>
          <div class="facet-item" :class="{ active: f.hasPdf }" @click="setF('hasPdf', !f.hasPdf)"><span><el-icon><Document /></el-icon> 仅有全文</span><span class="muted">{{ fmtNum(facets.hasPdf) }}</span></div>
          <div class="facet-title">发表年份</div>
          <div class="year-range">
            <el-input-number v-model="yr[0]" :controls="false" placeholder="起" size="small" />
            <span>–</span>
            <el-input-number v-model="yr[1]" :controls="false" placeholder="止" size="small" />
            <el-button size="small" @click="applyYears">确定</el-button>
          </div>
          <FacetList title="循证问题" :items="facets.eq" :model-value="f.eq" @update:model-value="setF('eq', $event)" />
          <FacetList title="研究设计" :items="facets.design" :model-value="f.design" @update:model-value="setF('design', $event)" :limit="10" />
          <FacetList title="语言" :items="facets.lang" :labels="LANG" :model-value="f.lang" @update:model-value="setF('lang', $event)" />
        </template>
        <template v-else-if="f.scope === 'recommendation'">
          <FacetList title="指南" :items="guidelineFacet" :model-value="f.guidelineId" @update:model-value="setF('guidelineId', $event)" />
          <FacetList title="推荐强度" :items="facets.strength" :labels="STRENGTH" :model-value="f.strength" @update:model-value="setF('strength', $event)" />
          <FacetList title="证据确信度" :items="facets.certainty" :labels="CERTAINTY" :model-value="f.certainty" @update:model-value="setF('certainty', $event)" />
          <FacetList title="建议行动" :items="facets.actionType" :labels="ACTION_TYPE" :model-value="f.actionType" @update:model-value="setF('actionType', $event)" />
        </template>
        <template v-else-if="f.scope === 'finding'">
          <FacetList title="发现类型" :items="facets.findingType" :labels="FINDING_TYPE" :model-value="f.findingType" @update:model-value="setF('findingType', $event)" />
          <FacetList title="效应指标" :items="facets.measure" :labels="MEASURE" :model-value="f.measure" @update:model-value="setF('measure', $event)" />
          <FacetList title="研究设计" :items="facets.design" :labels="DESIGN_COARSE" :model-value="f.design" @update:model-value="setF('design', $event)" />
          <FacetList title="陈述来源" :items="facets.statementRole" :labels="STATEMENT_ROLE" :model-value="f.statementRole" @update:model-value="setF('statementRole', $event)" />
        </template>
        <template v-else>
          <FacetList title="内容类型" :items="facets.blockType" :labels="BLOCK_TYPE" :model-value="f.blockType" @update:model-value="setF('blockType', $event)" />
          <FacetList title="所在文献" :items="recordFacet" :model-value="f.recordId" @update:model-value="setF('recordId', $event)" :limit="10" />
        </template>
      </aside>

      <section class="card results">
        <div class="toolbar" v-if="f.scope === 'record'">
          <span class="muted">{{ f.q ? "在标题、摘要、作者、DOI 中匹配" : "已结构化的文献排在前面" }}</span>
          <el-select v-model="f.sort" style="width: 130px; margin-left: auto" size="small" @change="run(1)">
            <el-option label="默认排序" value="" /><el-option label="年份降序" value="year_desc" /><el-option label="年份升序" value="year_asc" />
          </el-select>
        </div>
        <div v-loading="loading" style="min-height: 320px">
          <template v-if="f.scope === 'record'">
            <div v-for="r in rows" :key="r.recordId" class="result-item">
              <div class="result-title">
                <router-link :to="`/record/${r.recordId}`" v-html="highlight(r.title, f.q)" />
              </div>
              <div class="result-meta">
                <el-tag v-if="r.documentId" size="small" effect="dark">已结构化</el-tag>
                <el-tag v-if="r.designLabel" size="small" effect="plain">{{ r.designLabel }}</el-tag>
                <span>{{ r.pubYear || "年份不详" }}</span>
                <span v-if="r.sourceDb">{{ r.sourceDb }}</span>
                <span>{{ label(LANG, r.language) }}</span>
                <span v-if="r.hasPdf" class="has-pdf"><el-icon><Document /></el-icon>全文</span>
                <span v-for="q in r.questions || []" :key="q" class="q-tag">{{ q }}</span>
              </div>
              <div class="muted authors" v-if="r.authors">{{ r.authors }}</div>
              <div class="result-snippet" v-if="r.snippet" v-html="highlight(r.snippet, f.q)" />
            </div>
          </template>
          <template v-else-if="f.scope === 'recommendation'">
            <RecoCard v-for="r in rows" :key="r.recId" :r="r" :q="f.q" />
          </template>
          <template v-else-if="f.scope === 'finding'">
            <FindingCard v-for="x in rows" :key="x.findingId" :f="x" />
          </template>
          <template v-else>
            <div v-for="r in rows" :key="r.blockId" class="result-item">
              <div class="muted">
                <router-link :to="{ path: `/record/${r.recordId}`, query: { tab: 'fulltext', block: r.blockId } }">{{ r.recordTitle }}</router-link>
                （{{ r.pubYear }}）· {{ label(BLOCK_TYPE, r.blockType) }} · 第 {{ r.page ?? "?" }} 页<template v-if="r.section"> · {{ r.section }}</template>
                <el-tag v-if="r.spanCount" size="small" type="warning" effect="plain" style="margin-left: 6px">含 {{ r.spanCount }} 处溯源</el-tag>
              </div>
              <div class="result-snippet passage" v-html="highlight(r.snippet, f.q)" />
            </div>
          </template>
          <el-empty v-if="!loading && !rows.length" description="没有找到结果" />
        </div>
        <el-pagination style="margin-top: 12px; justify-content: flex-end" background layout="total, sizes, prev, pager, next" :total="total"
          v-model:current-page="f.pageNum" v-model:page-size="f.pageSize" :page-sizes="[10, 20, 50]" @change="run()" />
      </section>

      <aside>
        <div class="card"><h3 class="section-title">{{ chartTitles[0] }}</h3><div ref="c1" class="chart" /></div>
        <div class="card" style="margin-top: 16px"><h3 class="section-title">{{ chartTitles[1] }}</h3><div ref="c2" class="chart" /></div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { search } from "@/api";
import { useChart } from "@/chartTheme";
import { ACTION_TYPE, BLOCK_TYPE, CERTAINTY, DESIGN_COARSE, FINDING_TYPE, LANG, MEASURE, STATEMENT_ROLE, STRENGTH, STRENGTH_COLOR, fmtNum, highlight, label } from "@/labels";
import FacetList from "@/components/FacetList.vue";
import RecoCard from "@/components/RecoCard.vue";
import FindingCard from "@/components/FindingCard.vue";

const SCOPES = { record: "文献题录", recommendation: "推荐意见", finding: "研究发现", passage: "原文段落" };
const FILTERS = ["yearFrom", "yearTo", "design", "lang", "eq", "structured", "hasPdf", "strength", "certainty", "actionType", "guidelineId", "findingType", "measure", "statementRole", "recordId", "blockType", "conceptId"];
const route = useRoute(); const router = useRouter();

function fromQuery(qs) {
  const o = { scope: SCOPES[qs.scope] ? qs.scope : "record", q: qs.q || "", sort: qs.sort || "", pageNum: 1, pageSize: 20 };
  for (const k of FILTERS) o[k] = qs[k] ?? null;
  o.structured = !!qs.structured; o.hasPdf = !!qs.hasPdf;
  return o;
}
const f = reactive(fromQuery(route.query));
const yr = ref([f.yearFrom ? Number(f.yearFrom) : undefined, f.yearTo ? Number(f.yearTo) : undefined]);
const rows = ref([]); const total = ref(0); const facets = ref({}); const loading = ref(false);

const activeTags = computed(() => {
  const t = [];
  const add = (k, text) => f[k] != null && f[k] !== "" && f[k] !== false && t.push({ k, text });
  if (f.yearFrom || f.yearTo) t.push({ k: "years", text: `${f.yearFrom || "…"}–${f.yearTo || "…"} 年` });
  add("design", `设计：${label(DESIGN_COARSE, f.design)}`); add("lang", label(LANG, f.lang)); add("eq", `问题 ${f.eq}`);
  add("structured", "仅结构化"); add("hasPdf", "仅有全文"); add("strength", label(STRENGTH, f.strength)); add("certainty", `确信度 ${label(CERTAINTY, f.certainty)}`);
  add("actionType", label(ACTION_TYPE, f.actionType)); add("findingType", label(FINDING_TYPE, f.findingType)); add("measure", label(MEASURE, f.measure));
  add("statementRole", label(STATEMENT_ROLE, f.statementRole)); add("blockType", label(BLOCK_TYPE, f.blockType));
  if (f.guidelineId) t.push({ k: "guidelineId", text: "指定指南" });
  if (f.recordId) t.push({ k: "recordId", text: "指定文献" });
  if (f.conceptId) t.push({ k: "conceptId", text: `概念 #${f.conceptId}` });
  return t;
});
const guidelineFacet = computed(() => (facets.value.guideline || []).map((x) => ({ key: String(x.key), n: x.n, name: x.label })));
const recordFacet = computed(() => (facets.value.record || []).map((x) => ({ key: String(x.key), n: x.n, name: x.label })));

function setF(k, v) {
  if (k === "years") { f.yearFrom = f.yearTo = null; yr.value = [undefined, undefined]; }
  else f[k] = v;
  run(1);
}
function applyYears() { f.yearFrom = yr.value[0] || null; f.yearTo = yr.value[1] || null; run(1); }
function onScope() {
  for (const k of FILTERS) f[k] = k === "structured" || k === "hasPdf" ? false : null;
  yr.value = [undefined, undefined];
  run(1);
}

let seq = 0;
async function run(page) {
  if (page) f.pageNum = page;
  const my = ++seq; loading.value = true;
  try {
    const d = await search({ ...f });
    if (my !== seq) return;
    rows.value = d.rows; total.value = d.total; facets.value = d.facets;
  } finally { if (my === seq) loading.value = false; }
  const qs = { scope: f.scope };
  for (const k of ["q", "sort", ...FILTERS]) if (f[k] != null && f[k] !== "" && f[k] !== false) qs[k] = f[k] === true ? 1 : f[k];
  router.replace({ query: qs });
  await nextTick(); drawCharts();
}
watch(() => route.query.q, (q) => { if ((q || "") !== f.q) { f.q = q || ""; run(1); } });

const c1 = ref(); const c2 = ref(); const draw1 = useChart(c1); const draw2 = useChart(c2);
const chartTitles = computed(() => ({
  record: ["命中文献年度趋势", "研究设计分布"], recommendation: ["推荐强度分布", "建议行动类型"],
  finding: ["效应指标分布", "研究发现类型"], passage: ["内容类型", "命中最多的文献"],
}[f.scope]));
const hbar = (items, labels) => ({
  tooltip: {}, grid: { left: 100, right: 30, top: 4, bottom: 4 }, xAxis: { type: "value", show: false },
  yAxis: { type: "category", data: items.map((x) => (labels ? label(labels, x.key) : x.name || x.key)).reverse(), axisLabel: { width: 90, overflow: "truncate" } },
  series: [{ type: "bar", data: items.map((x) => x.n).reverse(), label: { show: true, position: "right", fontSize: 11, color: "#83939c" } }],
});
const pie = (items, labels, colors) => ({
  tooltip: { trigger: "item" }, legend: { type: "scroll", bottom: 0, textStyle: { fontSize: 11 } },
  series: [{ type: "pie", radius: ["40%", "66%"], center: ["50%", "42%"], label: { show: false },
    data: items.map((x) => ({ name: label(labels, x.key), value: x.n, itemStyle: colors?.[x.key] ? { color: colors[x.key] } : undefined })) }],
});
function drawCharts() {
  const v = facets.value;
  if (f.scope === "record") {
    const ys = [...(v.year || [])].filter((x) => x.key >= 1990).sort((a, b) => a.key - b.key);
    draw1({ tooltip: { trigger: "axis" }, grid: { left: 36, right: 8, top: 10, bottom: 22 }, xAxis: { type: "category", data: ys.map((x) => x.key) }, yAxis: { type: "value" }, series: [{ type: "line", symbol: "none", areaStyle: { opacity: 0.18 }, data: ys.map((x) => x.n) }] });
    draw2(hbar((v.design || []).slice(0, 8)));
  } else if (f.scope === "recommendation") {
    draw1(pie(v.strength || [], STRENGTH, STRENGTH_COLOR)); draw2(hbar(v.actionType || [], ACTION_TYPE));
  } else if (f.scope === "finding") {
    draw1(hbar((v.measure || []).slice(0, 10), MEASURE)); draw2(pie(v.findingType || [], FINDING_TYPE));
  } else {
    draw1(pie(v.blockType || [], BLOCK_TYPE)); draw2(hbar((v.record || []).slice(0, 8).map((x) => ({ ...x, name: (x.label || "").slice(0, 24) }))));
  }
}
onMounted(() => run());
</script>

<style scoped>
.scope-bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; padding: 12px 16px; }
.cond { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.total { margin-left: auto; }
.toolbar { display: flex; align-items: center; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid var(--c-border); }
.year-range { display: flex; align-items: center; gap: 4px; padding: 0 4px; }
.year-range .el-input-number { width: 64px; }
.authors { margin: 2px 0 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; }
.has-pdf { display: inline-flex; align-items: center; gap: 3px; color: var(--c-primary-600); }
.q-tag { font-family: var(--font-mono); font-size: 11.5px; color: var(--c-accent); border: 1px solid var(--c-accent-100); background: var(--c-accent-50); border-radius: 3px; padding: 0 5px; line-height: 18px; }
.passage { margin-top: 6px; font-family: var(--font-serif); }
</style>
