<template>
  <div v-if="spans?.length" class="spans">
    <button type="button" class="toggle" @click="open = !open" :aria-expanded="open">
      <el-icon><Document /></el-icon>原文依据 {{ spans.length }} 处<span v-if="pages">（第 {{ pages }} 页）</span>
      <el-icon class="caret" :class="{ open }"><ArrowDown /></el-icon>
    </button>
    <div v-if="open">
      <div v-for="s in spans" :key="s.spanId + (s.role || '')" class="quote">
        {{ s.quote }}
        <div class="meta">
          <span>第 {{ s.page ?? "?" }} 页</span>
          <span v-if="s.role && s.role !== 'source'">{{ ROLE[s.role] || s.role }}</span>
          <el-tag v-if="!s.quoteVerified" size="small" type="warning" effect="plain">未能在原文精确定位</el-tag>
          <router-link v-if="recordId && s.blockId" :to="{ path: `/record/${recordId}`, query: { tab: 'fulltext', block: s.blockId } }">在全文中查看 ›</router-link>
          <a v-if="recordId && hasPdf && s.page" :href="pdfUrl(recordId, s.page)" target="_blank" rel="noopener">PDF 第 {{ s.page }} 页 ›</a>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { pdfUrl } from "@/api";
const props = defineProps({ spans: Array, recordId: [Number, String], hasPdf: { type: Boolean, default: true }, defaultOpen: Boolean });
const open = ref(props.defaultOpen);
const ROLE = { adjustment: "校正因素", population_definition: "人群定义", eligibility: "纳排标准", definition: "定义" };
const pages = computed(() => [...new Set((props.spans || []).map((s) => s.page).filter((p) => p != null))].sort((a, b) => a - b).join("、"));
</script>

<style scoped>
.spans { margin-top: 8px; }
.toggle { display: inline-flex; align-items: center; gap: 5px; border: 0; background: none; padding: 2px 0; color: var(--c-primary-600); font: inherit; font-size: 12.5px; cursor: pointer; }
.toggle:hover { color: var(--c-primary); }
.caret { transition: transform 0.15s var(--ease); }
.caret.open { transform: rotate(180deg); }
</style>
