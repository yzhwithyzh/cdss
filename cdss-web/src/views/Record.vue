<template>
  <div class="page" v-loading="loading">
    <template v-if="rec">
      <div class="card head-card">
        <div class="badges">
          <el-tag v-if="kb" effect="dark">已深度结构化</el-tag>
          <el-tag v-else type="info" effect="plain">仅题录</el-tag>
          <el-tag v-if="kb" effect="plain">{{ label(DOC_KIND, kb.document.docKind) }}</el-tag>
          <el-tag v-if="rec.designLabel" type="warning" effect="plain">{{ rec.designLabel }}</el-tag>
          <router-link v-for="q in rec.questions" :key="q.code" :to="`/question/${q.code}`" class="q-tag" :title="q.label">{{ q.code }} {{ q.label }}</router-link>
        </div>
        <h1 class="page-title">{{ rec.title }}</h1>
        <div class="authors" v-if="rec.authors">{{ rec.authors }}</div>
        <div class="result-meta">
          <span>{{ rec.pubYear || "年份不详" }}</span>
          <span v-if="rec.sourceDb">{{ rec.sourceDb }}</span>
          <span>{{ label(LANG, rec.language) }}</span>
          <a v-if="rec.doi" :href="`https://doi.org/${rec.doi}`" target="_blank" rel="noopener">DOI: {{ rec.doi }}</a>
          <a v-if="rec.pmid" :href="`https://pubmed.ncbi.nlm.nih.gov/${rec.pmid}/`" target="_blank" rel="noopener">PMID: {{ rec.pmid }}</a>
          <a v-if="rec.hasPdf" :href="pdfUrl(rec.recordId)" target="_blank" rel="noopener"><el-icon><Document /></el-icon> 全文 PDF</a>
        </div>
      </div>

      <div class="layout-2" :class="{ full: tab === 'graph' }" style="margin-top: 16px">
        <section class="card">
          <el-tabs v-model="tab" @tab-change="onTab">
            <el-tab-pane label="概览" name="overview" />
            <el-tab-pane v-if="kb" label="知识图谱" name="graph" />
            <el-tab-pane v-if="kb?.studies.length || kb?.populations.length" :label="`研究与人群`" name="design" />
            <el-tab-pane v-if="kb?.findings.length" :label="`研究发现 ${kb.findings.length}`" name="findings" />
            <el-tab-pane v-if="kb?.recommendations.length" :label="`推荐意见 ${kb.recommendations.length}`" name="recommendations" />
            <el-tab-pane v-if="kb?.assertions.length || kb?.definitions.length" :label="`断言与定义 ${kb.assertions.length + kb.definitions.length}`" name="assertions" />
            <el-tab-pane v-if="kb" label="结构化全文" name="fulltext" />
            <el-tab-pane v-if="rec.hasPdf" label="PDF 原文" name="pdf" />
          </el-tabs>

          <!-- 概览 -->
          <div v-if="tab === 'overview'">
            <h3 class="section-title">摘要</h3>
            <p class="abstract" v-if="rec.abstract">{{ rec.abstract }}</p>
            <el-empty v-else description="无摘要" :image-size="60" />
            <h3 class="section-title" style="margin-top: 18px">研究设计分类</h3>
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="设计类型">{{ rec.designLabel || "-" }}</el-descriptions-item>
              <el-descriptions-item label="判定方式">{{ rec.designMethod || "-" }}<span v-if="rec.designConfidence != null" class="muted">（置信度 {{ Number(rec.designConfidence).toFixed(2) }}）</span></el-descriptions-item>
              <el-descriptions-item label="判定依据" :span="2">{{ rec.designRationale || "-" }}</el-descriptions-item>
            </el-descriptions>
            <template v-if="kb">
              <h3 class="section-title" style="margin-top: 18px">结构化信息</h3>
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="文献类型">{{ label(DOC_KIND, kb.document.docKind) }}</el-descriptions-item>
                <el-descriptions-item label="研究对象">{{ label(SUBJECT_SCOPE, kb.document.subjectScope) }}</el-descriptions-item>
                <el-descriptions-item label="全文来源">{{ kb.document.sourceFormat?.toUpperCase() }} · {{ kb.document.parseMethod === "ocr" ? "OCR 识别" : "文本层解析" }}</el-descriptions-item>
                <el-descriptions-item label="页数">{{ kb.document.pageCount || "-" }}</el-descriptions-item>
              </el-descriptions>
              <div class="kpis">
                <div class="kpi" v-for="k in kpis" :key="k.label" :class="{ link: k.tab }" @click="k.tab && (tab = k.tab)"><b>{{ k.n }}</b><span>{{ k.label }}</span></div>
              </div>
            </template>
            <el-alert v-else type="info" :closable="false" show-icon style="margin-top: 16px"
              title="这篇文献目前只有题录与研究设计分类，尚未进行全文结构化抽取。" />
          </div>

          <!-- 研究与人群 -->
          <div v-if="tab === 'design'">
            <div v-for="s in kb.studies" :key="s.studyId" class="block">
              <h3 class="section-title">研究信息<span class="extra" v-if="s.studyName">{{ s.studyName }}</span></h3>
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="设计">{{ label(DESIGN_COARSE, s.designCoarse) }}<span class="muted" v-if="s.designAsReported">（原文：{{ s.designAsReported }}）</span></el-descriptions-item>
                <el-descriptions-item label="国家/地区">{{ (s.countries || []).join("、") || "-" }}</el-descriptions-item>
                <el-descriptions-item label="中心数">{{ s.nCenters || "-" }}</el-descriptions-item>
                <el-descriptions-item label="研究期间">{{ s.periodAsReported || "-" }}</el-descriptions-item>
                <el-descriptions-item label="数据来源">{{ s.dataSource || "-" }}</el-descriptions-item>
                <el-descriptions-item label="注册号">{{ (s.registrationIds || []).join("、") || "-" }}</el-descriptions-item>
                <el-descriptions-item label="母研究" v-if="s.parentStudyName">{{ s.parentStudyName }}</el-descriptions-item>
              </el-descriptions>
            </div>

            <h3 class="section-title" style="margin-top: 18px" v-if="kb.populations.length">研究人群</h3>
            <div v-for="p in kb.populations" :key="p.populationId" class="pop" :class="{ child: p.parentPopulationId }">
              <div class="pop-head">
                <el-tag size="small" effect="plain">{{ label(POP_ROLE, p.role) }}</el-tag>
                <b>{{ p.label || "（未命名人群）" }}</b>
                <span v-if="p.nValue" class="muted">n = {{ fmtNum(p.nValue) }}{{ p.nUnit && p.nUnit !== "participants" ? " " + p.nUnit : "" }}</span>
                <span v-if="age(p)" class="muted">年龄 {{ age(p) }}</span>
                <span v-if="p.indicationSetting" class="muted">{{ p.indicationSetting }}</span>
              </div>
              <div v-for="(conds, kind) in groupBy(p.conditions, 'kind')" :key="kind" class="row-cond">
                <span class="kind">{{ label(POP_COND_KIND, kind) }}</span>
                <span v-for="(c, i) in conds" :key="i" class="pill" :class="{ cond: kind === 'inclusion' }">
                  <router-link v-if="c.conceptId" :to="`/concept/${c.conceptId}`">{{ c.concept }}</router-link>
                  <span v-if="c.valueAsReported" class="muted">{{ c.valueAsReported }}</span>
                </span>
              </div>
              <SpanQuotes :spans="p.spans" :record-id="rec.recordId" :has-pdf="rec.hasPdf" />
            </div>

            <template v-if="kb.arms.length">
              <h3 class="section-title" style="margin-top: 18px">组别与干预</h3>
              <el-table :data="kb.arms" size="small" border>
                <el-table-column label="组别" min-width="160"><template #default="{ row }"><b>{{ row.label }}</b></template></el-table-column>
                <el-table-column label="角色" width="100"><template #default="{ row }">{{ label(ARM_ROLE, row.role) }}</template></el-table-column>
                <el-table-column label="干预 / 暴露" min-width="260">
                  <template #default="{ row }">
                    <div v-for="(x, i) in row.interventions" :key="i">
                      <router-link v-if="x.conceptId" :to="`/concept/${x.conceptId}`">{{ x.concept }}</router-link>
                      <span class="muted">{{ [x.doseAsReported || fmtRange(x.doseLow, x.doseHigh, x.doseUnit), x.frequencyAsReported, x.route, fmtRange(x.durationLow, x.durationHigh, x.durationUnit)].filter(Boolean).join(" · ") }}</span>
                    </div>
                    <span v-if="!row.interventions.length" class="muted">-</span>
                  </template>
                </el-table-column>
              </el-table>
            </template>

            <template v-if="kb.outcomes.length">
              <h3 class="section-title" style="margin-top: 18px">结局指标</h3>
              <el-table :data="kb.outcomes" size="small" border>
                <el-table-column label="结局（原文）" prop="labelAsReported" min-width="200" />
                <el-table-column label="概念" min-width="150"><template #default="{ row }"><router-link v-if="row.conceptId" :to="`/concept/${row.conceptId}`">{{ row.concept }}</router-link><span v-else class="muted">-</span></template></el-table-column>
                <el-table-column label="主次" width="90"><template #default="{ row }">{{ label(PRIMACY, row.primacy) }}</template></el-table-column>
                <el-table-column label="时间点" width="140"><template #default="{ row }">{{ fmtRange(row.timepointLow, row.timepointHigh, row.timepointUnit) || "-" }}</template></el-table-column>
                <el-table-column label="判定方式" prop="ascertainment" min-width="120" />
              </el-table>
            </template>
          </div>

          <!-- 研究发现 -->
          <div v-if="tab === 'findings'">
            <div v-if="docForest.length" class="block">
              <h3 class="section-title">比值类效应汇总<span class="extra">{{ docForest.length }} 个 OR / HR / RR，绿色降低、红色升高、灰色不显著</span></h3>
              <ForestPlot :rows="docForest" label-title="暴露 → 结局" />
            </div>
            <div class="filter-row">
              <el-radio-group v-model="ftype" size="small">
                <el-radio-button value="">全部 {{ kb.findings.length }}</el-radio-button>
                <el-radio-button v-for="(n, k) in findingTypes" :key="k" :value="k">{{ label(FINDING_TYPE, k) }} {{ n }}</el-radio-button>
              </el-radio-group>
            </div>
            <FindingCard v-for="x in shownFindings" :key="x.findingId" :f="x" :show-source="false" />
          </div>

          <!-- 推荐意见 -->
          <div v-if="tab === 'recommendations'">
            <el-descriptions :column="2" border size="small" v-if="kb.guideline" class="block">
              <el-descriptions-item label="制定机构" :span="2">{{ kb.guideline.issuingBody || "-" }}</el-descriptions-item>
              <el-descriptions-item label="地区">{{ kb.guideline.region || "-" }}</el-descriptions-item>
              <el-descriptions-item label="发布">{{ kb.guideline.pubDate || rec.pubYear || "-" }}</el-descriptions-item>
              <el-descriptions-item label="替代版本" v-if="kb.guideline.supersedesAsReported" :span="2">{{ kb.guideline.supersedesAsReported }}</el-descriptions-item>
            </el-descriptions>
            <div class="filter-row">
              <el-radio-group v-model="strength" size="small">
                <el-radio-button value="">全部 {{ kb.recommendations.length }}</el-radio-button>
                <el-radio-button v-for="(n, k) in strengthCount" :key="k" :value="k">{{ label(STRENGTH, k) }} {{ n }}</el-radio-button>
              </el-radio-group>
              <router-link :to="{ path: '/compare' }" class="muted">与其他指南对比 ›</router-link>
            </div>
            <RecoCard v-for="r in shownRecs" :key="r.recId" :r="r" :show-guideline="false" />
          </div>

          <!-- 断言与定义 -->
          <div v-if="tab === 'assertions'">
            <template v-if="kb.assertions.length">
              <h3 class="section-title">概念断言<span class="extra">文中陈述的概念间关系</span></h3>
              <div v-for="a in kb.assertions" :key="a.assertionId" class="assert">
                <div class="triple">
                  <router-link v-if="a.subjectConceptId" :to="`/concept/${a.subjectConceptId}`" class="s">{{ a.subject }}</router-link>
                  <span class="p">{{ label(PREDICATE, a.predicate) }}</span>
                  <router-link v-if="a.objectConceptId" :to="`/concept/${a.objectConceptId}`" class="o">{{ a.object }}</router-link>
                  <span v-if="a.context" class="muted">（{{ a.context }}）</span>
                </div>
                <SpanQuotes :spans="a.spans" :record-id="rec.recordId" :has-pdf="rec.hasPdf" />
              </div>
            </template>
            <template v-if="kb.definitions.length">
              <h3 class="section-title" style="margin-top: 18px">术语定义</h3>
              <div v-for="x in kb.definitions" :key="x.definitionId" class="assert">
                <div class="triple"><router-link :to="`/concept/${x.conceptId}`" class="s">{{ x.concept }}</router-link><span v-if="x.variantLabel" class="muted">定义口径：{{ x.variantLabel }}</span></div>
                <SpanQuotes :spans="x.spans" :record-id="rec.recordId" :has-pdf="rec.hasPdf" default-open />
              </div>
            </template>
          </div>

          <KnowledgeGraph v-if="tab === 'graph'" :kb="kb" :record-id="rec.recordId" :focus="route.query.node" />
          <FulltextView v-if="tab === 'fulltext'" :record-id="rec.recordId" :focus-block="route.query.block" />
          <iframe v-if="tab === 'pdf'" :src="pdfUrl(rec.recordId, route.query.page)" class="pdf-frame" title="全文 PDF" />
        </section>

        <aside v-show="tab !== 'graph'">
          <div class="card" v-if="kb">
            <h3 class="section-title">抽取状态</h3>
            <div class="status-row"><span>审核状态</span><el-tag size="small" type="warning" effect="plain">待人工审核</el-tag></div>
            <div class="status-row"><span>校验提示</span><span>{{ kb.issues?.total || 0 }} 条</span></div>
            <div class="status-row"><span>原文定位</span><span>{{ spanCount }} 处引文</span></div>
            <p class="muted note">所有条目均由模型抽取，并已在原文中定位到对应句子；数值与结论请以原文为准。</p>
          </div>
          <div class="card" v-if="citedBy.length">
            <h3 class="section-title">被指南推荐引用<span class="extra">{{ citedBy.length }} 条</span></h3>
            <div v-for="r in citedBy" :key="r.recId" class="cited">
              <router-link :to="{ path: `/record/${r.recordId}`, query: { tab: 'recommendations' } }"><span class="num">{{ r.numberAsReported }}</span> {{ r.textVerbatim }}</router-link>
              <div class="muted">{{ r.guidelineTitle }}</div>
            </div>
          </div>
          <div class="card" v-if="relatedConcepts.length">
            <h3 class="section-title">涉及概念</h3>
            <div class="tag-row"><ConceptChip v-for="x in relatedConcepts" :key="x.id" :id="x.id" :name="x.name" :n="x.n" /></div>
          </div>
          <div class="card" v-if="!kb">
            <h3 class="section-title">相似主题</h3>
            <router-link v-if="rec.designLabel" :to="{ path: '/search', query: { design: rec.designLabel } }" class="facet-item"><span>同为「{{ rec.designLabel }}」的文献</span><el-icon><ArrowRight /></el-icon></router-link>
            <router-link v-for="q in rec.questions" :key="q.code" :to="`/question/${q.code}`" class="facet-item"><span>{{ q.code }} 证据地图</span><el-icon><ArrowRight /></el-icon></router-link>
          </div>
        </aside>
      </div>
    </template>
    <el-empty v-else-if="!loading" description="文献不存在" />
  </div>
</template>

<script setup>
import { getRecord, pdfUrl } from "@/api";
import { ARM_ROLE, DESIGN_COARSE, DOC_KIND, FINDING_TYPE, LANG, POP_COND_KIND, POP_ROLE, PREDICATE, PRIMACY, RATIO_MEASURES, STRENGTH, SUBJECT_SCOPE, fmtNum, fmtRange, label } from "@/labels";
import ConceptChip from "@/components/ConceptChip.vue";
import FindingCard from "@/components/FindingCard.vue";
import ForestPlot from "@/components/ForestPlot.vue";
import FulltextView from "@/components/FulltextView.vue";
import KnowledgeGraph from "@/components/KnowledgeGraph.vue";
import RecoCard from "@/components/RecoCard.vue";
import SpanQuotes from "@/components/SpanQuotes.vue";

const route = useRoute(); const router = useRouter();
const d = ref({}); const loading = ref(true);
const tab = ref(route.query.tab || "overview");
const ftype = ref(""); const strength = ref("");
const rec = computed(() => d.value.record);
const kb = computed(() => d.value.kb);
const citedBy = computed(() => d.value.citedBy || []);

function onTab(t) { router.replace({ query: { ...route.query, tab: t, block: t === "fulltext" ? route.query.block : undefined, node: t === "graph" ? route.query.node : undefined } }); }
watch(() => route.query.tab, (t) => { if (t && t !== tab.value) tab.value = t; });

const groupBy = (arr, k) => (arr || []).reduce((m, x) => ((m[x[k]] = m[x[k]] || []).push(x), m), {});
const age = (p) => fmtRange(p.ageLow, p.ageHigh, p.ageUnit === "years" ? "岁" : p.ageUnit);
const findingTypes = computed(() => (kb.value?.findings || []).reduce((m, x) => ((m[x.findingType] = (m[x.findingType] || 0) + 1), m), {}));
const shownFindings = computed(() => (kb.value?.findings || []).filter((x) => !ftype.value || x.findingType === ftype.value));
const strengthCount = computed(() => (kb.value?.recommendations || []).reduce((m, r) => ((m[r.strength] = (m[r.strength] || 0) + 1), m), {}));
const shownRecs = computed(() => (kb.value?.recommendations || []).filter((r) => !strength.value || r.strength === strength.value));
const spanCount = computed(() => {
  const k = kb.value; if (!k) return 0;
  return [...k.findings, ...k.recommendations, ...k.populations, ...k.assertions, ...k.definitions].reduce((a, x) => a + (x.spans?.length || 0), 0);
});
const kpis = computed(() => {
  const k = kb.value; if (!k) return [];
  return [
    { label: "研究", n: k.studies.length, tab: "design" }, { label: "人群", n: k.populations.length, tab: "design" },
    { label: "结局指标", n: k.outcomes.length, tab: "design" }, { label: "研究发现", n: k.findings.length, tab: "findings" },
    { label: "效应值", n: k.findings.reduce((a, x) => a + x.results.length, 0), tab: "findings" },
    { label: "推荐意见", n: k.recommendations.length, tab: "recommendations" }, { label: "断言", n: k.assertions.length, tab: "assertions" },
  ].filter((x) => x.n);
});
const docForest = computed(() => (kb.value?.findings || []).flatMap((x) => x.results
  .filter((r) => RATIO_MEASURES.has(r.measure) && r.value != null)
  .map((r) => ({
    label: `${x.exposureAsReported || x.exposureConcept || "暴露"} → ${x.outcomeLabel || x.outcomeConcept || "结局"}`,
    sub: [r.arm, r.category].filter(Boolean).join(" · "),
    value: Number(r.value), low: r.ciLow != null ? Number(r.ciLow) : null, high: r.ciHigh != null ? Number(r.ciHigh) : null, measure: r.measure,
  }))));
const relatedConcepts = computed(() => {
  const m = {};
  const add = (id, name) => { if (id && name) (m[id] = m[id] || { id, name, n: 0 }).n++; };
  for (const x of kb.value?.findings || []) { add(x.exposureConceptId, x.exposureConcept); add(x.comparatorConceptId, x.comparatorConcept); add(x.outcomeConceptId, x.outcomeConcept); }
  for (const r of kb.value?.recommendations || []) { r.conditions.forEach((c) => add(c.conceptId, c.concept)); r.actions.forEach((a) => add(a.targetConceptId, a.targetConcept)); }
  for (const a of kb.value?.assertions || []) { add(a.subjectConceptId, a.subject); add(a.objectConceptId, a.object); }
  return Object.values(m).sort((a, b) => b.n - a.n).slice(0, 30);
});

onMounted(async () => {
  try { d.value = await getRecord(route.params.id).catch(() => ({})); } finally { loading.value = false; }
  document.title = (rec.value?.title || "文献") + " · 循证知识库";
});
onBeforeUnmount(() => { document.title = "循证知识库"; });
</script>

<style scoped>
.head-card .badges { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.q-tag { font-size: 12px; color: #7a4614; border: 1px solid var(--c-accent-100); background: var(--c-accent-50); border-radius: 4px; padding: 0 8px; line-height: 22px; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.authors { color: var(--c-text-2); font-size: 13px; margin: 8px 0 2px; }
.abstract { font-family: var(--font-serif); font-size: 15px; line-height: 1.9; color: #24333a; white-space: pre-line; margin: 0; text-align: justify; }
.block { margin-bottom: 18px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 10px; margin-top: 16px; }
.kpi { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px; text-align: center; display: flex; flex-direction: column; }
.kpi.link { cursor: pointer; transition: border-color 0.15s var(--ease); }
.kpi.link:hover { border-color: var(--c-primary-600); }
.kpi b { font-size: 22px; color: var(--c-primary); font-variant-numeric: tabular-nums; }
.kpi span { font-size: 12.5px; color: var(--c-text-3); }
.pop { border: 1px solid var(--c-border); border-radius: var(--radius-sm); padding: 10px 14px; margin-bottom: 10px; }
.pop.child { margin-left: 24px; border-style: dashed; }
.pop-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.row-cond { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-top: 8px; font-size: 13px; }
.row-cond .kind { font-weight: 600; color: var(--c-text-2); width: 64px; flex-shrink: 0; }
.filter-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 4px 0 14px; flex-wrap: wrap; }
.assert { border-bottom: 1px dashed var(--c-border); padding: 10px 0; }
.triple { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 14.5px; }
.triple .s { font-weight: 600; color: var(--c-primary); }
.triple .p { font-size: 12.5px; color: #7a4614; background: var(--c-accent-50); border-radius: 4px; padding: 0 8px; }
.triple .o { font-weight: 600; color: var(--c-text); }
.pdf-frame { width: 100%; height: calc(100vh - 140px); min-height: 600px; border: 1px solid var(--c-border); border-radius: 4px; background: #f5f5f5; }
.status-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px dashed var(--c-border); font-size: 13px; color: var(--c-text-2); }
.note { margin: 10px 0 0; line-height: 1.6; font-size: 12.5px; }
.cited { padding: 8px 0; border-bottom: 1px dashed var(--c-border); font-size: 13px; line-height: 1.6; }
.cited a { color: var(--c-text); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.cited .num { font-family: var(--font-mono); font-size: 11.5px; color: var(--c-primary); background: var(--c-primary-50); border-radius: 3px; padding: 0 4px; }
aside .card + .card { margin-top: 16px; }
.layout-2.full { grid-template-columns: minmax(0, 1fr); }
</style>
