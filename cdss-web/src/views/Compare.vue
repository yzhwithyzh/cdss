<template>
  <div class="page">
    <div class="card">
      <h3 class="section-title">跨指南推荐对比<span class="extra">同一临床行动下，各指南的推荐方向、强度与随访间隔并排对照</span></h3>
      <div class="query">
        <el-input v-model="q" placeholder="按关键词筛选推荐，如 surveillance、piecemeal、cold snare" clearable style="max-width: 420px" @keyup.enter="run" @clear="run">
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" @click="run">对比</el-button>
        <span class="muted" v-if="conceptId">已限定概念 #{{ conceptId }} <a href="#" @click.prevent="conceptId = null; run()">清除</a></span>
      </div>
    </div>

    <div v-loading="loading">
      <div class="g-heads" :style="cols" v-if="d.guidelines?.length">
        <div class="g-head" v-for="g in d.guidelines" :key="g.guidelineId">
          <div class="region">{{ g.region || "地区未注明" }} · {{ g.pubYear || (g.pubDate || "").slice(0, 4) }}</div>
          <router-link :to="{ path: `/record/${g.recordId}`, query: { tab: 'recommendations' } }" class="title">{{ g.title }}</router-link>
          <div class="muted body">{{ g.issuingBody }}</div>
          <div class="g-stat"><b>{{ byGuideline[g.guidelineId]?.length || 0 }}</b> / {{ g.recs }} 条推荐匹配</div>
        </div>
      </div>

      <div class="card" style="margin-top: 16px" v-if="d.recommendations?.length">
        <h3 class="section-title">行动类型 × 指南<span class="extra">格内为推荐条数（强 / 弱）</span></h3>
        <table class="matrix">
          <thead><tr><th>建议行动</th><th v-for="g in d.guidelines" :key="g.guidelineId">{{ shortName(g) }}</th></tr></thead>
          <tbody>
            <tr v-for="a in actionTypes" :key="a" :class="{ on: a === focus }" @click="jump(a)">
              <td>{{ label(ACTION_TYPE, a) }}</td>
              <td v-for="g in d.guidelines" :key="g.guidelineId">
                <template v-if="cell(a, g.guidelineId).n">
                  <b>{{ cell(a, g.guidelineId).n }}</b>
                  <span class="muted">（{{ cell(a, g.guidelineId).strong }} / {{ cell(a, g.guidelineId).weak }}）</span>
                </template>
                <span v-else class="muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <template v-for="grp in groups" :key="grp.key">
        <div class="grp-title" :id="'grp-' + grp.key">{{ grp.title }}<span class="muted">{{ grp.total }} 条</span></div>
        <div class="side" :style="cols">
          <div v-for="g in d.guidelines" :key="g.guidelineId" class="col">
            <RecoCard v-for="r in grp.by[g.guidelineId] || []" :key="r.recId" :r="r" :q="q" :show-guideline="false" />
            <div v-if="!(grp.by[g.guidelineId] || []).length" class="none">该指南在此项下无推荐</div>
          </div>
        </div>
      </template>
      <el-empty v-if="!loading && !d.recommendations?.length" description="没有匹配的推荐意见" />
    </div>
  </div>
</template>

<script setup>
import { compare } from "@/api";
import { ACTION_TYPE, label } from "@/labels";
import RecoCard from "@/components/RecoCard.vue";
const route = useRoute();
const q = ref(route.query.q || ""); const conceptId = ref(route.query.conceptId || null);
const d = ref({}); const loading = ref(false); const focus = ref(null);
const cols = computed(() => ({ gridTemplateColumns: `repeat(${d.value.guidelines?.length || 1}, minmax(0, 1fr))` }));
const byGuideline = computed(() => (d.value.recommendations || []).reduce((m, r) => ((m[r.guidelineId] = m[r.guidelineId] || []).push(r), m), {}));
const shortName = (g) => `${g.region || ""} ${g.pubYear || ""}`.trim() || `指南 ${g.guidelineId}`;
const actionTypes = computed(() => {
  const n = {};
  for (const x of d.value.matrix || []) n[x.actionType] = (n[x.actionType] || 0) + x.n;
  return Object.keys(n).sort((a, b) => n[b] - n[a]);
});
function cell(a, gid) {
  const xs = (d.value.matrix || []).filter((x) => x.actionType === a && x.guidelineId === gid);
  return { n: xs.reduce((s, x) => s + x.n, 0), strong: xs.filter((x) => x.strength === "strong").reduce((s, x) => s + x.n, 0), weak: xs.filter((x) => x.strength === "weak").reduce((s, x) => s + x.n, 0) };
}
// 每条推荐按其第一个行动类型归组；没有行动的单独一组
const groups = computed(() => {
  const order = [...actionTypes.value, "_none"];
  const m = {};
  for (const r of d.value.recommendations || []) {
    const k = r.actions?.[0]?.actionType || "_none";
    const grp = (m[k] = m[k] || { key: k, title: k === "_none" ? "未标注具体行动（风险因素、背景性陈述等）" : label(ACTION_TYPE, k), by: {}, total: 0 });
    (grp.by[r.guidelineId] = grp.by[r.guidelineId] || []).push(r); grp.total++;
  }
  return order.filter((k) => m[k]).map((k) => m[k]);
});
function jump(a) { focus.value = a; document.getElementById("grp-" + a)?.scrollIntoView({ behavior: "smooth", block: "start" }); }
async function run() {
  loading.value = true;
  try { d.value = await compare({ q: q.value.trim(), conceptId: conceptId.value }); } finally { loading.value = false; }
}
onMounted(run);
</script>

<style scoped>
.query { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.g-heads { display: grid; gap: 16px; margin-top: 16px; }
.g-head { background: #fff; border: 1px solid var(--c-border); border-top: 3px solid var(--c-primary-600); border-radius: var(--radius); padding: 14px 18px; box-shadow: var(--shadow-sm); }
.g-head:nth-child(2) { border-top-color: var(--c-accent); }
.region { font-size: 12px; font-weight: 700; letter-spacing: 1px; color: var(--c-text-3); }
.title { display: block; font-family: var(--font-serif); font-size: 17px; font-weight: 700; color: var(--c-text); margin: 4px 0; line-height: 1.45; }
.body { font-size: 12.5px; line-height: 1.5; }
.g-stat { margin-top: 8px; font-size: 13px; color: var(--c-text-2); }
.g-stat b { color: var(--c-primary); font-size: 18px; }
.matrix { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.matrix th { text-align: left; font-weight: 600; color: var(--c-text-2); background: #f6f9f9; padding: 8px 12px; border-bottom: 1px solid var(--c-border); }
.matrix td { padding: 8px 12px; border-bottom: 1px dashed var(--c-border); font-variant-numeric: tabular-nums; }
.matrix tbody tr { cursor: pointer; transition: background 0.15s var(--ease); }
.matrix tbody tr:hover, .matrix tr.on { background: var(--c-primary-50); }
.matrix b { color: var(--c-primary); }
.grp-title { margin: 24px 0 10px; font-size: 16px; font-weight: 700; display: flex; align-items: baseline; gap: 10px; padding-bottom: 6px; border-bottom: 2px solid var(--c-border); scroll-margin-top: 80px; }
.side { display: grid; gap: 16px; align-items: start; }
.col { min-width: 0; }
.none { border: 1px dashed var(--c-border-strong); border-radius: var(--radius-sm); padding: 18px; text-align: center; color: var(--c-text-3); font-size: 13px; background: #fafcfc; }
@media (max-width: 900px) { .g-heads, .side { grid-template-columns: minmax(0, 1fr) !important; } }
</style>
