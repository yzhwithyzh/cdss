<template>
  <div class="fulltext" v-loading="loading">
    <div class="ft-bar">
      <el-switch v-model="onlySpans" active-text="只看含溯源片段的内容" />
      <span class="muted">共 {{ blocks.length }} 个内容块，{{ spans.length }} 处溯源片段</span>
      <span class="legend"><i class="mk" />被抽取引用的原文</span>
    </div>
    <template v-for="b in shown" :key="b.blockId">
      <div v-if="b.pageBreak" class="page-break"><span>第 {{ b.page }} 页</span></div>
      <component :is="'h' + Math.min(6, (b.headingLevel || 1) + 2)" v-if="b.blockType === 'heading'" :id="'b-' + b.blockId" class="ft-h" :class="{ focus: focus === b.blockId }">{{ b.text }}</component>
      <figure v-else-if="b.imgPath" :id="'b-' + b.blockId" class="ft-fig" :class="{ focus: focus === b.blockId }">
        <img :src="imageUrl(recordId, b.imgPath)" :alt="b.text || '图'" loading="lazy" />
        <figcaption v-if="b.text && b.text.length > 2" v-html="render(b)" />
      </figure>
      <div v-else-if="b.blockType === 'table'" :id="'b-' + b.blockId" class="ft-table" :class="{ focus: focus === b.blockId }">
        <div class="cap" v-if="b.text">{{ b.text.split("\n")[0] }}</div>
        <div class="tbl" v-if="b.html" v-html="cleanTable(b.html)" />
      </div>
      <p v-else :id="'b-' + b.blockId" class="ft-p" :class="[{ focus: focus === b.blockId, li: b.blockType === 'list_item', cap: b.blockType === 'caption' }]" v-html="render(b)" />
    </template>
    <el-empty v-if="!loading && !blocks.length" description="该文献没有结构化全文" />
  </div>
</template>

<script setup>
import { getFulltext, imageUrl } from "@/api";
import { SPAN_ENTITY, esc } from "@/labels";
const props = defineProps({ recordId: [String, Number], focusBlock: [String, Number] });
const blocks = ref([]); const spans = ref([]); const loading = ref(true); const onlySpans = ref(false);
const focus = ref(props.focusBlock ? Number(props.focusBlock) : null);

const spansByBlock = computed(() => {
  const m = {};
  for (const s of spans.value) (m[s.blockId] = m[s.blockId] || []).push(s);
  return m;
});
const shown = computed(() => {
  let prev = null;
  return blocks.value
    .filter((b) => !onlySpans.value || spansByBlock.value[b.blockId] || b.blockType === "heading")
    .map((b) => { const pageBreak = b.page != null && b.page !== prev; prev = b.page ?? prev; return { ...b, pageBreak }; });
});

// 解析文本里残留的 <sup>/<sub> 字面标签：转义后再还原这两种，其余一律保持转义
const restoreSup = (html) => html.replace(/&lt;(\/?)(sup|sub)&gt;/g, "<$1$2>");

// 把同一块里的片段（可能重叠）合并成不重叠的区间，再切分文本加 <mark>
function render(b) {
  const text = b.text || "";
  const list = (spansByBlock.value[b.blockId] || []).filter((s) => s.charStart != null && s.charEnd > s.charStart).sort((a, c) => a.charStart - c.charStart);
  if (!list.length) return restoreSup(esc(text));
  const merged = [];
  for (const s of list) {
    const last = merged[merged.length - 1];
    if (last && s.charStart <= last.end) { last.end = Math.max(last.end, s.charEnd); s.entityTypes?.forEach((t) => last.types.add(t)); }
    else merged.push({ start: s.charStart, end: Math.min(s.charEnd, text.length), types: new Set(s.entityTypes || []) });
  }
  let out = "", pos = 0;
  for (const m of merged) {
    const title = [...m.types].filter(Boolean).map((t) => SPAN_ENTITY[t] || t).join("、");
    out += esc(text.slice(pos, m.start)) + `<mark class="span-mk" title="${esc(title)}">${esc(text.slice(m.start, m.end))}</mark>`;
    pos = m.end;
  }
  return restoreSup(out + esc(text.slice(pos)));
}

// 表格 HTML 来自 PDF 解析结果，只保留表格相关标签和合并单元格属性
const ALLOWED = new Set(["TABLE", "THEAD", "TBODY", "TFOOT", "TR", "TD", "TH", "BR", "SUB", "SUP", "B", "I", "STRONG", "EM", "SPAN"]);
function cleanTable(html) {
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
  const walk = (el) => {
    for (const child of [...el.children]) {
      if (!ALLOWED.has(child.tagName)) { child.replaceWith(doc.createTextNode(child.textContent)); continue; }
      for (const a of [...child.attributes]) if (!["colspan", "rowspan"].includes(a.name)) child.removeAttribute(a.name);
      walk(child);
    }
  };
  const root = doc.body.firstElementChild;
  walk(root);
  return root.innerHTML;
}

async function scrollToFocus() {
  if (!focus.value) return;
  await nextTick();
  document.getElementById("b-" + focus.value)?.scrollIntoView({ behavior: "smooth", block: "center" });
}
watch(() => props.focusBlock, (v) => { focus.value = v ? Number(v) : null; scrollToFocus(); });
onMounted(async () => {
  try {
    const d = await getFulltext(props.recordId);
    blocks.value = d.blocks; spans.value = d.spans;
  } finally { loading.value = false; }
  scrollToFocus();
});
</script>

<style scoped>
.fulltext { max-width: 860px; margin: 0 auto; min-height: 300px; }
.ft-bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; padding: 6px 0 12px; border-bottom: 1px solid var(--c-border); margin-bottom: 8px; position: sticky; top: 64px; background: #fff; z-index: 2; }
.legend { margin-left: auto; font-size: 12.5px; color: var(--c-text-3); display: inline-flex; align-items: center; gap: 6px; }
.legend .mk { width: 22px; height: 10px; background: linear-gradient(transparent 40%, #f6d9a8 40%); display: inline-block; }
.page-break { text-align: center; margin: 18px 0 8px; position: relative; color: var(--c-text-3); font-size: 12px; }
.page-break::before { content: ""; position: absolute; left: 0; right: 0; top: 50%; border-top: 1px dashed var(--c-border-strong); }
.page-break span { position: relative; background: #fff; padding: 0 10px; }
.ft-h { font-family: var(--font-serif); margin: 18px 0 6px; line-height: 1.45; }
h3.ft-h { font-size: 18px; } h4.ft-h { font-size: 16px; } h5.ft-h, h6.ft-h { font-size: 15px; }
.ft-p { font-family: var(--font-serif); font-size: 15px; line-height: 1.9; color: #24333a; margin: 8px 0; text-align: justify; }
.ft-p.li { padding-left: 16px; }
.ft-p.cap { font-size: 13px; color: var(--c-text-2); font-family: var(--font-sans); }
.ft-fig { margin: 12px 0; text-align: center; }
.ft-fig img { max-width: 100%; border: 1px solid var(--c-border); border-radius: 4px; }
.ft-fig figcaption { font-size: 13px; color: var(--c-text-2); margin-top: 6px; text-align: left; }
.ft-table { margin: 12px 0; }
.ft-table .cap { font-size: 13px; font-weight: 600; color: var(--c-text); margin-bottom: 6px; }
.tbl { overflow-x: auto; }
.tbl :deep(table) { border-collapse: collapse; font-size: 12.5px; min-width: 100%; }
.tbl :deep(td), .tbl :deep(th) { border: 1px solid var(--c-border-strong); padding: 4px 8px; vertical-align: top; }
.tbl :deep(tr:first-child td) { background: #f6f9f9; font-weight: 600; }
:deep(.span-mk) { background: linear-gradient(transparent 40%, #f6d9a8 40%); color: inherit; font-weight: inherit; cursor: help; }
.focus { animation: flash 2.4s var(--ease); border-radius: 4px; }
@keyframes flash { 0%, 30% { background: var(--c-accent-50); box-shadow: 0 0 0 6px var(--c-accent-50); } 100% { background: transparent; box-shadow: none; } }
</style>
