<template>
  <section class="hero">
    <div class="hero-inner">
      <div class="hero-eyebrow"><Logo :size="28" /><span>Evidence Knowledge Base · {{ s.topic?.topicCode }}</span></div>
      <h1 class="hero-title">{{ s.topic?.topicName || "" }}循证知识库</h1>
      <p class="hero-sub">从 {{ fmtNum(c.records) }} 篇题录，到逐句可回溯原文的推荐意见与研究发现</p>
      <div class="hero-search-row">
        <SearchInput v-model="kw" class="hero-search" size="large" placeholder="输入关键词、概念或文献标题，如 piecemeal resection、锯齿状病变" />
        <button class="hero-btn" type="button" @click="go"><el-icon><Search /></el-icon>检索</button>
      </div>
      <router-link to="/chat" class="hero-chat"><el-icon><ChatDotRound /></el-icon>用自然语言提问：描述患者情况，获取各指南建议与出处 ›</router-link>
      <div class="hero-examples">
        <span>试试：</span>
        <button v-for="e in examples" :key="e" class="chip" type="button" @click="kw = e; go()">{{ e }}</button>
      </div>
    </div>
  </section>

  <div class="page">
    <div class="layers" v-loading="!c.records">
      <div class="layer">
        <div class="layer-head"><span class="layer-tag">文献层</span><span class="muted">全量题录，按研究设计与临床问题组织</span></div>
        <div class="layer-stats">
          <router-link class="stat" to="/search"><div class="stat-num">{{ fmtNum(c.records) }}</div><div class="stat-label">文献题录</div></router-link>
          <router-link class="stat" :to="{ path: '/search', query: { hasPdf: 1 } }"><div class="stat-num">{{ fmtNum(c.fulltexts) }}</div><div class="stat-label">可阅读全文</div></router-link>
          <div class="stat"><div class="stat-num">{{ fmtNum(designTotal) }}</div><div class="stat-label">已分类研究设计</div></div>
          <router-link class="stat" to="/question"><div class="stat-num">{{ fmtNum(c.taggedRecords) }}</div><div class="stat-label">关联循证问题</div></router-link>
        </div>
      </div>
      <div class="layer deep">
        <div class="layer-head"><span class="layer-tag">结构化证据层</span><span class="muted">试点文献逐条抽取，每条均定位到原文</span></div>
        <div class="layer-stats six">
          <router-link class="stat" :to="{ path: '/search', query: { structured: 1 } }"><div class="stat-num">{{ fmtNum(c.structuredDocs) }}</div><div class="stat-label">深度结构化文献</div></router-link>
          <router-link class="stat" :to="{ path: '/search', query: { scope: 'recommendation' } }"><div class="stat-num">{{ fmtNum(c.recommendations) }}</div><div class="stat-label">推荐意见</div></router-link>
          <router-link class="stat" :to="{ path: '/search', query: { scope: 'finding' } }"><div class="stat-num">{{ fmtNum(c.findings) }}</div><div class="stat-label">研究发现</div></router-link>
          <div class="stat"><div class="stat-num">{{ fmtNum(c.results) }}</div><div class="stat-label">效应值</div></div>
          <router-link class="stat" to="/concepts"><div class="stat-num">{{ fmtNum(c.concepts) }}</div><div class="stat-label">医学概念</div></router-link>
          <router-link class="stat" :to="{ path: '/search', query: { scope: 'passage' } }"><div class="stat-num">{{ fmtNum(c.spans) }}</div><div class="stat-label">原文溯源片段</div></router-link>
        </div>
      </div>
    </div>

    <div class="card" style="margin-top: 16px">
      <h3 class="section-title">循证问题<span class="extra">按临床问题组织的证据入口</span></h3>
      <div class="questions">
        <router-link v-for="q in s.questions || []" :key="q.code" :to="`/question/${q.code}`" class="q-card">
          <span class="q-code">{{ q.code }}</span>
          <span class="q-label">{{ q.label }}</span>
          <span class="q-n"><b>{{ fmtNum(q.records) }}</b> 篇文献</span>
        </router-link>
      </div>
    </div>

    <div class="grid-3" style="margin-top: 16px">
      <div class="card"><h3 class="section-title">文献年度分布<span class="extra">1990 年至今</span></h3><div ref="yearRef" class="chart" /></div>
      <div class="card"><h3 class="section-title">研究设计分布</h3><div ref="designRef" class="chart" /></div>
      <div class="card"><h3 class="section-title">来源数据库</h3><div ref="sourceRef" class="chart" /></div>
    </div>
    <div class="grid-3" style="margin-top: 16px">
      <div class="card"><h3 class="section-title">结构化文献类型</h3><div ref="kindRef" class="chart" /></div>
      <div class="card"><h3 class="section-title">研究发现类型</h3><div ref="ftypeRef" class="chart" /></div>
      <div class="card"><h3 class="section-title">效应指标</h3><div ref="measureRef" class="chart" /></div>
    </div>

    <div class="card" style="margin-top: 16px">
      <h3 class="section-title">高频概念<span class="extra">按被推荐意见、研究发现、断言引用的次数排序 · <router-link to="/concepts">全部概念 ›</router-link></span></h3>
      <div class="tag-row">
        <ConceptChip v-for="x in s.topConcepts || []" :key="x.conceptId" :id="x.conceptId" :name="x.name" :type="x.conceptType" :n="x.n" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { useChart } from "@/chartTheme";
import { loadStats } from "@/store";
import { DOC_KIND, FINDING_TYPE, MEASURE, fmtNum, label } from "@/labels";
import Logo from "@/components/Logo.vue";
import SearchInput from "@/components/SearchInput.vue";
import ConceptChip from "@/components/ConceptChip.vue";
const router = useRouter();
const kw = ref(""); const s = ref({});
const c = computed(() => s.value.counts || {});
const designTotal = computed(() => (s.value.byDesign || []).reduce((a, x) => a + x.n, 0));
const examples = ["piecemeal resection", "surveillance interval", "锯齿状病变", "adenoma detection rate", "cold snare"];
function go() { if (kw.value.trim()) router.push({ path: "/search", query: { q: kw.value.trim() } }); }

const yearRef = ref(); const designRef = ref(); const sourceRef = ref(); const kindRef = ref(); const ftypeRef = ref(); const measureRef = ref();
const charts = [yearRef, designRef, sourceRef, kindRef, ftypeRef, measureRef].map(useChart);
const hbar = (rows, labels, onClick) => ({
  tooltip: {}, grid: { left: 110, right: 30, top: 4, bottom: 4, containLabel: false },
  xAxis: { type: "value", show: false },
  yAxis: { type: "category", data: rows.map((x) => label(labels || {}, x.key)).reverse(), axisLabel: { width: 100, overflow: "truncate" } },
  series: [{ type: "bar", data: rows.map((x) => x.n).reverse(), label: { show: true, position: "right", fontSize: 11, color: "#83939c" } }],
});
const pie = (rows, labels) => ({
  tooltip: { trigger: "item" }, legend: { type: "scroll", bottom: 0, itemGap: 10, textStyle: { fontSize: 11 } },
  series: [{ type: "pie", radius: ["40%", "66%"], center: ["50%", "42%"], label: { show: false }, data: rows.map((x) => ({ name: label(labels || {}, x.key), value: x.n })) }],
});
onMounted(async () => {
  s.value = await loadStats();
  await nextTick();
  const v = s.value;
  const [year, design, source, kind, ftype, measure] = charts;
  year({ tooltip: { trigger: "axis" }, grid: { left: 40, right: 12, top: 12, bottom: 24 }, xAxis: { type: "category", data: v.byYear.map((x) => x.key) }, yAxis: { type: "value" },
    series: [{ type: "line", symbol: "none", areaStyle: { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(29,111,122,.28)" }, { offset: 1, color: "rgba(29,111,122,0)" }] } }, data: v.byYear.map((x) => x.n) }] });
  const dc = design(hbar(v.byDesign.slice(0, 10)));
  dc?.on("click", (e) => router.push({ path: "/search", query: { design: e.name } }));
  source(pie(v.bySource.slice(0, 8)));
  kind(hbar(v.byDocKind, DOC_KIND));
  ftype(pie(v.byFindingType, FINDING_TYPE));
  measure(hbar(v.byMeasure.slice(0, 10), MEASURE));
});
</script>

<style scoped>
.hero { position: relative; overflow: hidden; color: #fff; padding: 60px 20px 72px; background: linear-gradient(135deg, #0b3a42 0%, #145560 55%, #1d6f7a 100%); }
.hero::before { content: ""; position: absolute; inset: 0; background: radial-gradient(600px 300px at 12% 110%, rgba(227, 163, 92, 0.25), transparent 60%), radial-gradient(500px 260px at 90% -10%, rgba(255, 255, 255, 0.14), transparent 60%); pointer-events: none; }
.hero::after { content: ""; position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,.04) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.04) 1px, transparent 1px); background-size: 32px 32px; mask-image: linear-gradient(180deg, rgba(0,0,0,.6), transparent); pointer-events: none; }
.hero-inner { position: relative; z-index: 1; max-width: 880px; margin: 0 auto; text-align: center; }
.hero-eyebrow { display: inline-flex; align-items: center; gap: 10px; font-size: 12px; letter-spacing: 2px; text-transform: uppercase; color: rgba(255,255,255,.72); margin-bottom: 14px; }
.hero-title { font-family: var(--font-serif); font-size: 40px; font-weight: 700; letter-spacing: 4px; margin: 0; color: #fff; line-height: 1.25; }
.hero-sub { margin: 12px 0 28px; font-size: 15px; color: rgba(255,255,255,.82); }
.hero-search-row { display: flex; box-shadow: 0 10px 30px rgba(0,0,0,.22); border-radius: 12px; }
.hero-btn { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; flex-shrink: 0; border: none; border-radius: 0 12px 12px 0; background: var(--c-accent); color: #fff; height: 52px; padding: 0 28px; font-size: 15px; font-weight: 600; cursor: pointer; font-family: inherit; transition: background 0.15s var(--ease); }
.hero-btn:hover { background: #ad6a28; }
.hero-examples { margin-top: 16px; display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; font-size: 13px; color: rgba(255,255,255,.7); }
.hero-chat { display: inline-flex; align-items: center; gap: 6px; margin-top: 14px; color: #fff; font-size: 14px; background: rgba(227,163,92,.22); border: 1px solid rgba(227,163,92,.5); border-radius: 999px; padding: 5px 16px; }
.hero-chat:hover { color: #fff; background: rgba(227,163,92,.35); }
.chip { border: 1px solid rgba(255,255,255,.28); background: rgba(255,255,255,.1); color: #fff; font-size: 13px; padding: 4px 12px; border-radius: 999px; cursor: pointer; font-family: inherit; transition: background 0.15s var(--ease); }
.chip:hover { background: rgba(255,255,255,.22); }

.layers { display: grid; grid-template-columns: 4fr 6fr; gap: 16px; margin-top: -48px; position: relative; z-index: 2; }
.layer { background: #fff; border: 1px solid var(--c-border); border-radius: var(--radius); padding: 14px 16px 16px; box-shadow: var(--shadow-md); }
.layer.deep { border-top: 3px solid var(--c-accent); }
.layer:not(.deep) { border-top: 3px solid var(--c-primary-600); }
.layer-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.layer-tag { font-weight: 700; font-size: 14px; color: var(--c-text); }
.layer-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.layer-stats.six { grid-template-columns: repeat(6, 1fr); }
.stat { display: block; text-align: center; padding: 8px 4px; border-radius: 8px; transition: background 0.15s var(--ease); }
a.stat:hover { background: var(--c-primary-50); }
.stat-num { font-size: 24px; font-weight: 700; color: var(--c-primary); font-variant-numeric: tabular-nums; line-height: 1.2; }
.deep .stat-num { color: #9a5a1c; }
.stat-label { margin-top: 2px; font-size: 12.5px; color: var(--c-text-3); }

.questions { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.q-card { display: flex; flex-direction: column; gap: 6px; padding: 14px; border: 1px solid var(--c-border); border-radius: var(--radius-sm); background: linear-gradient(180deg, #fff, #f8fbfb); transition: border-color 0.15s var(--ease), box-shadow 0.15s var(--ease), transform 0.15s var(--ease); }
.q-card:hover { border-color: var(--c-primary-600); box-shadow: var(--shadow-md); transform: translateY(-1px); }
.q-code { font-family: var(--font-mono); font-weight: 700; color: var(--c-accent); font-size: 13px; }
.q-label { color: var(--c-text); font-weight: 600; line-height: 1.5; flex: 1; }
.q-n { color: var(--c-text-3); font-size: 12.5px; }
.q-n b { color: var(--c-primary); font-size: 15px; }
@media (max-width: 1100px) {
  .layers { grid-template-columns: 1fr; }
  .questions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .layer-stats.six { grid-template-columns: repeat(3, 1fr); }
}
</style>

<style>
.hero-search { flex: 1; }
.hero-search .el-input__wrapper { border-radius: 12px 0 0 12px; box-shadow: none; padding-left: 16px; font-size: 15px; height: 52px; }
.hero-search .el-input__wrapper.is-focus { box-shadow: inset 0 0 0 2px var(--c-accent-100); }
</style>
