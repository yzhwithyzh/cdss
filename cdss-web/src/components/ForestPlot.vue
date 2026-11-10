<template>
  <div class="forest" v-if="items.length">
    <div class="fp-grid fp-head">
      <span>{{ labelTitle }}</span>
      <span class="axis-note">← 降低　{{ measureText }} · 对数轴 · 95% CI　升高 →</span>
      <span>效应值</span>
    </div>
    <div v-for="(r, i) in items" :key="i" class="fp-grid fp-row">
      <div class="fp-label" :title="r.label + (r.sub ? '\n' + r.sub : '')">
        <router-link v-if="r.to" :to="r.to">{{ r.label }}</router-link><span v-else>{{ r.label }}</span>
        <div class="sub" v-if="r.sub">{{ r.sub }}</div>
      </div>
      <div class="fp-plot" role="img" :aria-label="fmt(r)">
        <i class="null" :style="{ left: x(1) + '%' }" />
        <i v-if="r.low != null && r.high != null" class="ci" :style="{ left: x(r.low) + '%', width: x(r.high) - x(r.low) + '%' }" />
        <i :class="['pt', sig(r)]" :style="{ left: x(r.value) + '%' }" />
      </div>
      <div class="fp-val">{{ fmt(r) }}</div>
    </div>
    <div class="fp-grid">
      <span />
      <div class="fp-axis"><span v-for="t in ticks" :key="t" :style="{ left: x(t) + '%' }">{{ t }}</span></div>
      <span />
    </div>
  </div>
</template>

<script setup>
import { MEASURE } from "@/labels";
// rows: [{ label, sub, value, low, high, measure, to }]，只放比值类指标（OR/HR/RR）
const props = defineProps({ rows: Array, labelTitle: { type: String, default: "比较" } });
const items = computed(() => (props.rows || []).filter((r) => r.value > 0 && (r.low == null || r.low > 0)));
const measureText = computed(() => [...new Set(items.value.map((r) => MEASURE[r.measure] || r.measure))].join(" / "));
const dom = computed(() => {
  const vals = items.value.flatMap((r) => [r.value, r.low, r.high]).filter((v) => v > 0);
  const lo = Math.max(0.01, Math.min(1, ...vals) / 1.3), hi = Math.min(100, Math.max(1, ...vals) * 1.3);
  return [Math.log10(lo), Math.log10(hi)];
});
const x = (v) => {
  const [a, b] = dom.value;
  const c = Math.min(Math.max(Math.log10(Math.max(v, 1e-3)), a), b);
  return 2 + ((c - a) / (b - a)) * 96;
};
const ticks = computed(() => {
  const [a, b] = dom.value;
  return [0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100].filter((t) => Math.log10(t) >= a && Math.log10(t) <= b);
});
const sig = (r) => (r.low == null || r.high == null ? "" : r.high < 1 ? "down" : r.low > 1 ? "up" : "ns");
const fmt = (r) => `${MEASURE[r.measure] || r.measure} ${r.value}${r.low != null && r.high != null ? ` (${r.low}–${r.high})` : ""}`;
</script>

<style scoped>
.forest { font-size: 12.5px; }
.fp-grid { display: grid; grid-template-columns: minmax(140px, 36%) minmax(0, 1fr) 150px; gap: 12px; align-items: center; }
.fp-head { color: var(--c-text-3); font-size: 12px; padding-bottom: 6px; border-bottom: 1px solid var(--c-border); }
.axis-note { text-align: center; }
.fp-row { padding: 5px 0; border-bottom: 1px dashed #edf2f3; }
.fp-label { overflow: hidden; line-height: 1.4; }
.fp-label > a, .fp-label > span, .fp-label .sub { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-label > span { color: var(--c-text); }
.fp-label .sub { font-size: 11.5px; color: var(--c-text-3); }
.fp-plot { position: relative; height: 22px; }
.fp-plot i { position: absolute; display: block; }
.null { top: 0; bottom: 0; width: 0; border-left: 1px dashed var(--c-text-3); }
.ci { top: 10px; height: 2px; background: var(--c-text-2); border-radius: 1px; }
.pt { top: 6px; width: 10px; height: 10px; margin-left: -5px; background: #93a1aa; border-radius: 2px; }
.pt.up { background: var(--c-danger); }
.pt.down { background: var(--c-success); }
.fp-val { font-variant-numeric: tabular-nums; color: var(--c-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fp-axis { position: relative; height: 18px; border-top: 1px solid var(--c-border-strong); }
.fp-axis span { position: absolute; top: 2px; transform: translateX(-50%); color: var(--c-text-3); font-size: 11px; }
</style>
