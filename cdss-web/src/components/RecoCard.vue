<template>
  <article class="ev-card" :class="'s-' + r.strength" :id="'rec-' + r.recId">
    <div class="head">
      <span class="num" v-if="r.numberAsReported">{{ r.numberAsReported }}</span>
      <div class="text" v-html="highlight(r.textVerbatim, q)" />
    </div>
    <div class="row">
      <el-tag size="small" :type="STRENGTH_TAG[r.strength]">{{ label(STRENGTH, r.strength) }}</el-tag>
      <el-tag size="small" type="info" effect="plain">证据确信度 {{ label(CERTAINTY, r.certainty) }}</el-tag>
      <span class="muted" v-if="r.strengthAsReported || r.certaintyAsReported">原文：{{ [r.strengthAsReported, r.certaintyAsReported].filter(Boolean).join("，") }}</span>
    </div>
    <div class="row" v-if="r.conditions?.length">
      <b>适用条件</b>
      <span v-for="(c, i) in r.conditions" :key="i" class="pill cond">
        <router-link v-if="c.conceptId" :to="`/concept/${c.conceptId}`">{{ c.concept }}</router-link>
        <span v-else>{{ c.fieldLabel || c.ruleFieldCode }}</span>
        <span v-if="condValue(c)" class="muted">{{ condValue(c) }}</span>
      </span>
    </div>
    <div class="row" v-if="r.actions?.length">
      <b>建议行动</b>
      <span v-for="(a, i) in r.actions" :key="i" class="pill act" :title="a.textAsReported">
        {{ label(ACTION_TYPE, a.actionType) }}
        <router-link v-if="a.targetConceptId" :to="`/concept/${a.targetConceptId}`">· {{ a.targetConcept }}</router-link>
        <span v-if="interval(a)">· {{ interval(a) }}</span>
      </span>
    </div>
    <div class="row" v-if="r.citations?.length">
      <button type="button" class="link-btn" @click="showCites = !showCites" :aria-expanded="showCites">
        <el-icon><Connection /></el-icon>引用文献 {{ r.citations.length }} 篇<span v-if="matched">，其中 {{ matched }} 篇已关联到库内文献</span>
        <el-icon class="caret" :class="{ open: showCites }"><ArrowDown /></el-icon>
      </button>
    </div>
    <ol v-if="showCites" class="cites">
      <li v-for="c in r.citations" :key="c.seq">
        <span class="ref">{{ c.refLabel }}</span>
        <div v-if="c.matchedRecordId" class="match">
          <el-tag size="small" :type="c.matchStatus === 'confirmed' ? 'success' : 'warning'" effect="plain">{{ label(MATCH_STATUS, c.matchStatus) }}</el-tag>
          <router-link :to="`/record/${c.matchedRecordId}`">{{ c.matchedTitle }}</router-link>
          <span class="muted">{{ c.matchedYear }}<template v-if="c.matchedDesign"> · {{ c.matchedDesign }}</template></span>
          <el-tag v-if="c.matchedStructured" size="small" effect="dark">已结构化</el-tag>
        </div>
      </li>
    </ol>
    <SpanQuotes :spans="r.spans" :record-id="r.recordId" />
    <div class="src" v-if="showGuideline">
      来源：<router-link :to="{ path: `/record/${r.recordId}`, query: { tab: 'recommendations' } }">{{ r.guidelineTitle }}</router-link>
      <span v-if="r.pubYear">（{{ r.pubYear }}<template v-if="r.region">，{{ r.region }}</template>）</span>
    </div>
  </article>
</template>

<script setup>
import SpanQuotes from "./SpanQuotes.vue";
import { ACTION_TYPE, CERTAINTY, MATCH_STATUS, STRENGTH, STRENGTH_TAG, fmtRange, highlight, label } from "@/labels";
const props = defineProps({ r: Object, q: { type: String, default: "" }, showGuideline: { type: Boolean, default: true } });
const showCites = ref(false);
const matched = computed(() => (props.r.citations || []).filter((c) => c.matchedRecordId).length);
const QUAL = { present: "存在", absent: "不存在", measured: "测量值", not_present: "不存在" };
function condValue(c) {
  const v = fmtRange(c.valueLow, c.valueHigh, c.valueUnit);
  return c.valueAsReported || v || (c.qualifier && c.qualifier !== "present" ? QUAL[c.qualifier] || c.qualifier : "");
}
function interval(a) {
  const v = fmtRange(a.intervalLow, a.intervalHigh, a.intervalUnit);
  return v ? `间隔 ${v}${a.anchor ? `（自${a.anchor}）` : ""}` : "";
}
</script>

<style scoped>
.link-btn { display: inline-flex; align-items: center; gap: 5px; border: 0; background: none; padding: 0; color: var(--c-primary-600); font: inherit; font-size: 12.5px; cursor: pointer; }
.caret { transition: transform 0.15s var(--ease); }
.caret.open { transform: rotate(180deg); }
.cites { margin: 6px 0 0; padding-left: 20px; font-size: 12.5px; color: var(--c-text-2); line-height: 1.6; }
.cites li { margin-bottom: 6px; }
.match { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 2px; }
</style>
