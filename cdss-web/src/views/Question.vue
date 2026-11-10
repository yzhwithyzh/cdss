<template>
  <div class="page">
    <div class="q-tabs">
      <router-link v-for="q in list" :key="q.code" :to="`/question/${q.code}`" class="q-tab" :class="{ on: q.code === code }">
        <span class="code">{{ q.code }}</span><span class="lbl">{{ q.label }}</span><span class="n">{{ fmtNum(q.records) }}</span>
      </router-link>
    </div>

    <div v-loading="loading" style="min-height: 300px">
      <template v-if="d.question">
        <div class="card head">
          <div>
            <div class="eyebrow">循证问题 {{ d.question.code }}</div>
            <h1 class="page-title">{{ d.question.label }}</h1>
          </div>
          <div class="nums">
            <router-link :to="{ path: '/search', query: { eq: code } }"><b>{{ fmtNum(d.question.records) }}</b><span>相关文献 ›</span></router-link>
            <div><b>{{ d.structured.length }}</b><span>已结构化</span></div>
            <div><b>{{ d.findings.length }}</b><span>研究发现</span></div>
          </div>
        </div>
        <el-alert type="info" :closable="false" show-icon style="margin-top: 12px"
          title="文献与问题的关联由题录标题关键词规则生成（kw-v2），用于组织检索入口与证据地图，不代表文献一定回答了该问题。" />

        <div class="grid-2" style="margin-top: 16px">
          <div class="card"><h3 class="section-title">研究设计构成</h3><div ref="designRef" class="chart tall" /></div>
          <div class="card"><h3 class="section-title">发表年度趋势</h3><div ref="yearRef" class="chart tall" /></div>
        </div>
        <div class="card" style="margin-top: 16px">
          <h3 class="section-title">证据地图<span class="extra">研究设计 × 发表年代（5 年一档），点击格子查看文献</span></h3>
          <div ref="mapRef" class="map" />
        </div>

        <div class="card" style="margin-top: 16px">
          <h3 class="section-title">已结构化的文献<span class="extra">{{ d.structured.length }} 篇</span></h3>
          <div v-for="r in d.structured" :key="r.recordId" class="result-item">
            <div class="result-title"><router-link :to="`/record/${r.recordId}`">{{ r.title }}</router-link></div>
            <div class="result-meta"><span>{{ r.pubYear }}</span><el-tag size="small" effect="plain" v-if="r.designLabel">{{ r.designLabel }}</el-tag><span>研究发现 {{ r.findings }}</span><span v-if="r.recs">推荐意见 {{ r.recs }}</span></div>
          </div>
          <el-empty v-if="!d.structured.length" :image-size="70" description="该问题下的文献尚未进入深度结构化（当前仅 40 篇试点文献完成抽取）" />
        </div>
        <div class="card" style="margin-top: 16px" v-if="d.findings.length">
          <h3 class="section-title">研究发现</h3>
          <FindingCard v-for="x in d.findings" :key="x.findingId" :f="x" />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { getQuestion, questions } from "@/api";
import { useChart } from "@/chartTheme";
import { fmtNum } from "@/labels";
import FindingCard from "@/components/FindingCard.vue";

const route = useRoute(); const router = useRouter();
const list = ref([]); const d = ref({}); const loading = ref(true);
const code = computed(() => route.params.code || "Q1");
const designRef = ref(); const yearRef = ref(); const mapRef = ref();
const drawDesign = useChart(designRef); const drawYear = useChart(yearRef); const drawMap = useChart(mapRef);

onMounted(async () => {
  list.value = await questions();
  if (!route.params.code && list.value.length) { router.replace(`/question/${list.value[0].code}`); return; }
  try { d.value = await getQuestion(code.value).catch(() => ({})); } finally { loading.value = false; }
  await nextTick();
  const v = d.value;
  if (!v.question) return;
  const top = v.byDesign.slice(0, 12);
  drawDesign({ tooltip: {}, grid: { left: 130, right: 40, top: 4, bottom: 4 }, xAxis: { type: "value", show: false },
    yAxis: { type: "category", data: top.map((x) => x.key).reverse(), axisLabel: { width: 120, overflow: "truncate" } },
    series: [{ type: "bar", data: top.map((x) => x.n).reverse(), label: { show: true, position: "right", fontSize: 11, color: "#83939c" } }] })
    ?.on("click", (e) => router.push({ path: "/search", query: { eq: code.value, design: e.name } }));
  drawYear({ tooltip: { trigger: "axis" }, grid: { left: 36, right: 10, top: 10, bottom: 24 }, xAxis: { type: "category", data: v.byYear.map((x) => x.key) },
    yAxis: { type: "value" }, series: [{ type: "bar", itemStyle: { borderRadius: [3, 3, 0, 0] }, data: v.byYear.map((x) => x.n) }] });

  const designs = top.map((x) => x.key);
  const periods = [...new Set(v.matrix.map((x) => x.period))].sort((a, b) => a - b);
  const cells = v.matrix.filter((x) => designs.includes(x.design)).map((x) => [periods.indexOf(x.period), designs.indexOf(x.design), x.n]);
  const max = Math.max(1, ...cells.map((x) => x[2]));
  drawMap({
    tooltip: { formatter: (p) => `${designs[p.value[1]]}<br/>${periods[p.value[0]]}–${periods[p.value[0]] + 4} 年：<b>${p.value[2]}</b> 篇` },
    grid: { left: 130, right: 20, top: 10, bottom: 50 },
    xAxis: { type: "category", data: periods.map((p) => `${p}–${String(p + 4).slice(2)}`), splitArea: { show: true } },
    yAxis: { type: "category", data: designs, splitArea: { show: true }, axisLabel: { width: 120, overflow: "truncate" } },
    visualMap: { min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, inRange: { color: ["#eef6f6", "#89b0b5", "#1d6f7a", "#0e434c"] } },
    series: [{ type: "heatmap", data: cells, label: { show: true, fontSize: 11, formatter: (p) => (p.value[2] ? p.value[2] : "") }, itemStyle: { borderColor: "#fff", borderWidth: 2 } }],
  })?.on("click", (e) => {
    const p = periods[e.value[0]];
    router.push({ path: "/search", query: { eq: code.value, design: designs[e.value[1]], yearFrom: p, yearTo: p + 4 } });
  });
});
</script>

<style scoped>
.q-tabs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }
.q-tab { display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: var(--radius-sm); border: 1px solid var(--c-border); background: #fff; transition: border-color 0.15s var(--ease); }
.q-tab:hover { border-color: var(--c-primary-600); }
.q-tab.on { border-color: var(--c-primary); background: var(--c-primary-50); box-shadow: inset 0 -3px 0 var(--c-accent); }
.q-tab .code { font-family: var(--font-mono); font-weight: 700; color: var(--c-accent); font-size: 12.5px; }
.q-tab .lbl { color: var(--c-text); font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.q-tab .n { color: var(--c-text-3); font-size: 12px; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
.eyebrow { font-family: var(--font-mono); color: var(--c-accent); font-size: 13px; font-weight: 700; margin-bottom: 4px; }
.nums { display: flex; }
.nums > * { display: flex; flex-direction: column; align-items: center; padding: 4px 18px; border-left: 1px solid var(--c-border); }
.nums b { font-size: 24px; color: var(--c-primary); font-variant-numeric: tabular-nums; line-height: 1.2; }
.nums span { font-size: 12px; color: var(--c-text-3); }
.chart.tall { height: 300px; }
.map { height: 440px; }
@media (max-width: 1100px) { .q-tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
