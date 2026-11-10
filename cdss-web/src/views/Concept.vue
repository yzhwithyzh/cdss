<template>
  <div class="page" v-loading="loading">
    <template v-if="c">
      <div class="card head">
        <div class="type" :style="{ background: CONCEPT_COLOR[c.conceptType] }">{{ label(CONCEPT_TYPE, c.conceptType) }}</div>
        <div class="names">
          <h1 class="page-title">{{ c.nameZh || c.nameEn }}</h1>
          <div class="en" v-if="c.nameZh && c.nameEn">{{ c.nameEn }}</div>
        </div>
        <div class="nums">
          <div><b>{{ d.findings.length }}</b><span>研究发现</span></div>
          <div><b>{{ d.recommendations.length }}</b><span>推荐意见</span></div>
          <div><b>{{ d.records.length }}</b><span>结构化文献</span></div>
          <router-link :to="{ path: '/search', query: { q: c.nameEn || c.nameZh } }"><b>{{ fmtNum(d.mentionCount) }}</b><span>题录标题提及 ›</span></router-link>
        </div>
      </div>

      <div class="layout-2" style="margin-top: 16px">
        <section class="card">
          <el-tabs v-model="tab">
            <el-tab-pane :label="`研究发现 ${d.findings.length}`" name="findings" :disabled="!d.findings.length" />
            <el-tab-pane :label="`推荐意见 ${d.recommendations.length}`" name="recs" :disabled="!d.recommendations.length" />
            <el-tab-pane :label="`概念断言 ${d.assertions.length}`" name="assertions" :disabled="!d.assertions.length" />
            <el-tab-pane :label="`人群条件 ${d.populations.length}`" name="populations" :disabled="!d.populations.length" />
          </el-tabs>

          <div v-if="tab === 'findings'">
            <div v-if="forest.length" style="margin-bottom: 18px">
              <h3 class="section-title">跨研究效应对比<span class="extra">涉及该概念的 OR / HR / RR，按文献分行</span></h3>
              <ForestPlot :rows="forest" label-title="文献 · 比较" />
            </div>
            <FindingCard v-for="x in d.findings" :key="x.findingId" :f="x" />
          </div>
          <div v-if="tab === 'recs'"><RecoCard v-for="r in d.recommendations" :key="r.recId" :r="r" /></div>
          <div v-if="tab === 'assertions'">
            <div v-for="a in d.assertions" :key="a.assertionId" class="assert">
              <div class="triple">
                <router-link :to="`/concept/${a.subjectConceptId}`" :class="{ self: a.subjectConceptId === c.conceptId }">{{ a.subject }}</router-link>
                <span class="p">{{ label(PREDICATE, a.predicate) }}</span>
                <router-link v-if="a.objectConceptId" :to="`/concept/${a.objectConceptId}`" :class="{ self: a.objectConceptId === c.conceptId }">{{ a.object }}</router-link>
                <span v-if="a.context" class="muted">（{{ a.context }}）</span>
              </div>
              <SpanQuotes :spans="a.spans" :record-id="a.recordId" />
              <div class="muted src">来源：<router-link :to="`/record/${a.recordId}`">{{ a.recordTitle }}</router-link></div>
            </div>
          </div>
          <div v-if="tab === 'populations'">
            <el-table :data="d.populations" size="small" border>
              <el-table-column label="条件类型" width="100"><template #default="{ row }">{{ label(POP_COND_KIND, row.kind) }}</template></el-table-column>
              <el-table-column label="原文取值" prop="valueAsReported" min-width="140" />
              <el-table-column label="人群" prop="populationLabel" min-width="180" />
              <el-table-column label="文献" min-width="240"><template #default="{ row }"><router-link :to="{ path: `/record/${row.recordId}`, query: { tab: 'design' } }">{{ row.recordTitle }}</router-link></template></el-table-column>
            </el-table>
          </div>
          <el-empty v-if="!d.findings.length && !d.recommendations.length && !d.assertions.length && !d.populations.length" description="该概念暂未被结构化证据引用" />
        </section>

        <aside>
          <div class="card" v-if="c.aliases.length">
            <h3 class="section-title">同义词与别名<span class="extra">{{ c.aliases.length }}</span></h3>
            <div class="tag-row"><span v-for="a in c.aliases" :key="a.alias + a.lang" class="pill" :title="a.source">{{ a.alias }}</span></div>
          </div>
          <div class="card" v-if="d.relations.length">
            <h3 class="section-title">概念关系</h3>
            <div v-for="(r, i) in d.relations" :key="i" class="rel">
              <template v-if="relOtherIsSubject(r.relType, r.dir)">
                <ConceptChip :id="r.conceptId" :name="r.name" :type="r.conceptType" /><span class="muted">{{ label(REL_TYPE, r.relType) }} 本概念</span>
              </template>
              <template v-else>
                <span class="muted">本概念 {{ label(REL_TYPE, r.relType) }}</span><ConceptChip :id="r.conceptId" :name="r.name" :type="r.conceptType" />
              </template>
              <el-tag v-if="r.source === 'manual'" size="small" type="warning" effect="plain" title="人工/AI 补录，无库内原文">无库内原文</el-tag>
              <el-tag v-else-if="r.source === 'dx_vocab'" size="small" type="info" effect="plain" title="门诊鉴别诊断受控词表，AI 审核（demo），非临床">词表 · AI 审核 demo</el-tag>
              <router-link v-for="sp in r.spans || []" :key="sp.spanId" class="rel-quote" :to="`/record/${sp.recordId}?tab=fulltext&block=${sp.blockId}`" title="查看原文">
                【{{ sp.sourceId }}】<template v-if="sp.page">第{{ sp.page }}页 </template>“{{ sp.quote.length > 140 ? sp.quote.slice(0, 140) + '…' : sp.quote }}”
              </router-link>
            </div>
          </div>
          <div class="card" v-if="d.records.length">
            <h3 class="section-title">相关结构化文献</h3>
            <div v-for="r in d.records" :key="r.recordId" class="doc">
              <router-link :to="`/record/${r.recordId}`">{{ r.title }}</router-link>
              <div class="muted">{{ r.pubYear }}<template v-if="r.designLabel"> · {{ r.designLabel }}</template></div>
            </div>
          </div>
          <div class="card">
            <h3 class="section-title">在全部题录中</h3>
            <p class="muted" style="margin: 0 0 8px">按名称与别名在标题中字面匹配，约 <b>{{ fmtNum(d.mentionCount) }}</b> 篇。</p>
            <div class="tag-row">
              <router-link v-for="t in d.mentionTerms.slice(0, 6)" :key="t" :to="{ path: '/search', query: { q: t } }" class="pill">{{ t }} ›</router-link>
            </div>
          </div>
        </aside>
      </div>
    </template>
    <el-empty v-else-if="!loading" description="概念不存在" />
  </div>
</template>

<script setup>
import { getConcept } from "@/api";
import { CONCEPT_COLOR, CONCEPT_TYPE, POP_COND_KIND, PREDICATE, RATIO_MEASURES, REL_TYPE, fmtNum, label, relOtherIsSubject } from "@/labels";
import ConceptChip from "@/components/ConceptChip.vue";
import FindingCard from "@/components/FindingCard.vue";
import ForestPlot from "@/components/ForestPlot.vue";
import RecoCard from "@/components/RecoCard.vue";
import SpanQuotes from "@/components/SpanQuotes.vue";

const route = useRoute();
const d = ref({}); const loading = ref(true); const tab = ref("findings");
const c = computed(() => d.value.concept);
const forest = computed(() => (d.value.findings || []).flatMap((x) => x.results
  .filter((r) => RATIO_MEASURES.has(r.measure) && r.value != null)
  .map((r) => ({
    label: `${(x.recordTitle || "").slice(0, 40)}${x.pubYear ? `（${x.pubYear}）` : ""}`,
    sub: [`${x.exposureAsReported || x.exposureConcept || ""} → ${x.outcomeLabel || x.outcomeConcept || ""}`, r.category].filter(Boolean).join(" · "),
    value: Number(r.value), low: r.ciLow != null ? Number(r.ciLow) : null, high: r.ciHigh != null ? Number(r.ciHigh) : null,
    measure: r.measure, to: { path: `/record/${x.recordId}`, query: { tab: "findings" } },
  }))));
onMounted(async () => {
  try { d.value = await getConcept(route.params.id).catch(() => ({})); } finally { loading.value = false; }
  const v = d.value;
  if (v.concept) {
    tab.value = v.findings.length ? "findings" : v.recommendations.length ? "recs" : v.assertions.length ? "assertions" : "populations";
    document.title = (v.concept.nameZh || v.concept.nameEn) + " · 循证知识库";
  }
});
onBeforeUnmount(() => { document.title = "循证知识库"; });
</script>

<style scoped>
.head { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
.type { color: #fff; font-size: 12.5px; font-weight: 600; padding: 4px 10px; border-radius: 6px; flex-shrink: 0; }
.names { flex: 1; min-width: 240px; }
.en { color: var(--c-text-2); font-size: 14px; margin-top: 2px; }
.nums { display: flex; gap: 8px; }
.nums > * { display: flex; flex-direction: column; align-items: center; padding: 6px 14px; border-left: 1px solid var(--c-border); }
.nums b { font-size: 22px; color: var(--c-primary); font-variant-numeric: tabular-nums; line-height: 1.2; }
.nums span { font-size: 12px; color: var(--c-text-3); }
.assert { border-bottom: 1px dashed var(--c-border); padding: 10px 0; }
.triple { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 14.5px; font-weight: 600; }
.triple a { color: var(--c-text); }
.triple a.self { color: var(--c-primary); }
.triple .p { font-weight: 500; font-size: 12.5px; color: #7a4614; background: var(--c-accent-50); border-radius: 4px; padding: 0 8px; }
.src { margin-top: 4px; font-size: 12.5px; }
.rel { display: flex; gap: 8px; align-items: center; padding: 4px 0; flex-wrap: wrap; }
.rel-quote { flex-basis: 100%; font-size: 12px; line-height: 1.5; color: var(--c-text-2, #666); padding-left: 10px; border-left: 2px solid var(--c-border); }
.doc { padding: 7px 0; border-bottom: 1px dashed var(--c-border); font-size: 13px; line-height: 1.5; }
.doc a { color: var(--c-text); }
aside .card + .card { margin-top: 16px; }
</style>
