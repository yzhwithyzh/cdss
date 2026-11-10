<template>
  <article class="ev-card k-finding" :id="'finding-' + f.findingId">
    <div class="head">
      <el-tag size="small" effect="plain">{{ label(FINDING_TYPE, f.findingType) }}</el-tag>
      <el-tag size="small" type="info" effect="plain" v-if="f.statementRole && f.statementRole !== 'own_result'">{{ label(STATEMENT_ROLE, f.statementRole) }}</el-tag>
      <div class="text pico">
        <template v-if="exposure">
          <router-link v-if="f.exposureConceptId" :to="`/concept/${f.exposureConceptId}`" class="e">{{ exposure }}</router-link>
          <span v-else class="e">{{ exposure }}</span>
          <template v-if="comparator">
            <span class="vs">vs</span>
            <router-link v-if="f.comparatorConceptId" :to="`/concept/${f.comparatorConceptId}`" class="c">{{ comparator }}</router-link>
            <span v-else class="c">{{ comparator }}</span>
          </template>
          <span class="arrow">→</span>
        </template>
        <router-link v-if="f.outcomeConceptId" :to="`/concept/${f.outcomeConceptId}`" class="o">{{ outcome }}</router-link>
        <span v-else class="o">{{ outcome }}</span>
        <el-tag v-if="f.outcomePrimacy === 'primary'" size="small" type="danger" effect="plain">主要结局</el-tag>
      </div>
    </div>
    <div class="row" v-if="f.populationLabel || f.modelType || f.pooledK">
      <span v-if="f.populationLabel"><b>人群</b> {{ f.populationLabel }}<span v-if="f.populationN" class="muted">（n={{ fmtNum(f.populationN) }}）</span></span>
      <span v-if="f.modelType"><b>模型</b> {{ f.modelType }}</span>
      <span v-if="f.pooledK"><b>合并</b> {{ f.pooledK }} 项研究<span v-if="f.pooledN">，{{ fmtNum(f.pooledN) }} 例</span></span>
    </div>
    <table class="res-table" v-if="f.results?.length">
      <thead><tr><th>指标</th><th>分组 / 类别</th><th>数值</th><th>原文</th><th>P</th></tr></thead>
      <tbody>
        <tr v-for="x in f.results" :key="x.resultId">
          <td>{{ label(MEASURE, x.measure) }}</td>
          <td>{{ [x.arm, x.category].filter(Boolean).join(" · ") || "-" }}</td>
          <td class="v">{{ value(x) }}</td>
          <td>{{ x.valueAsReported }}</td>
          <td>{{ x.pAsReported || "-" }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="forest.length > 1" style="margin-top: 10px"><ForestPlot :rows="forest" label-title="分组 / 类别" /></div>
    <SpanQuotes :spans="f.spans" :record-id="f.recordId" />
    <div class="src" v-if="showSource && f.recordId">
      来源：<router-link :to="{ path: `/record/${f.recordId}`, query: { tab: 'findings' } }">{{ f.recordTitle }}</router-link>
      <span v-if="f.pubYear">（{{ f.pubYear }}<template v-if="f.designCoarse">，{{ label(DESIGN_COARSE, f.designCoarse) }}</template>）</span>
    </div>
  </article>
</template>

<script setup>
import ForestPlot from "./ForestPlot.vue";
import SpanQuotes from "./SpanQuotes.vue";
import { DESIGN_COARSE, FINDING_TYPE, MEASURE, RATIO_MEASURES, STATEMENT_ROLE, fmtNum, label } from "@/labels";
const props = defineProps({ f: Object, showSource: { type: Boolean, default: true } });
const exposure = computed(() => props.f.exposureAsReported || props.f.exposureConcept || props.f.exposureArm);
const comparator = computed(() => props.f.comparatorAsReported || props.f.comparatorConcept || props.f.comparatorArm);
const outcome = computed(() => props.f.outcomeLabel || props.f.outcomeConcept || "（未标注结局）");
function value(x) {
  if (x.numerator != null && x.denominator != null) return `${x.numerator}/${x.denominator}${x.value != null ? ` (${Number(x.value)}${x.unit === "%" ? "%" : ""})` : ""}`;
  if (x.value == null) return "-";
  const ci = x.ciLow != null && x.ciHigh != null ? ` [${Number(x.ciLow)}, ${Number(x.ciHigh)}]` : "";
  return `${Number(x.value)}${x.unit && x.unit !== "ratio" ? " " + x.unit : ""}${ci}`;
}
// 同一条发现下有多个比值结果（不同亚组/类别）时才画小森林图，单个值看表格就够
const forest = computed(() => (props.f.results || [])
  .filter((x) => RATIO_MEASURES.has(x.measure) && x.value != null)
  .map((x) => ({ label: [x.arm, x.category].filter(Boolean).join(" · ") || exposure.value || "效应", value: Number(x.value),
    low: x.ciLow != null ? Number(x.ciLow) : null, high: x.ciHigh != null ? Number(x.ciHigh) : null, measure: x.measure })));
</script>

<style scoped>
.pico { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; font-size: 14.5px; }
.pico .e { color: var(--c-primary); font-weight: 600; }
.pico .c { color: var(--c-text-2); font-weight: 500; }
.pico .o { color: #8a3d12; font-weight: 600; }
.pico .vs, .pico .arrow { color: var(--c-text-3); font-size: 12.5px; }
.row b { margin-right: 4px; }
</style>
