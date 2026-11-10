<template>
  <template v-if="items?.length">
    <div class="facet-title">{{ title }}</div>
    <div class="facet-item" :class="{ active: modelValue == null || modelValue === '' }" @click="$emit('update:modelValue', null)"><span>全部</span></div>
    <div v-for="t in shown" :key="t.key" class="facet-item" :class="{ active: String(modelValue) === String(t.key) }"
      :title="text(t)" @click="$emit('update:modelValue', String(modelValue) === String(t.key) ? null : t.key)">
      <span>{{ text(t) }}</span><span class="muted">{{ fmtNum(t.n) }}</span>
    </div>
    <div v-if="items.length > limit && !all" class="more" @click="all = true">展开全部 {{ items.length }} 项 ›</div>
  </template>
</template>

<script setup>
import { fmtNum } from "@/labels";
const props = defineProps({ title: String, items: Array, modelValue: [String, Number], labels: Object, limit: { type: Number, default: 8 } });
defineEmits(["update:modelValue"]);
const all = ref(false);
const shown = computed(() => (all.value ? props.items : props.items.slice(0, props.limit)));
const text = (t) => t.name || (t.label ? `${t.key} ${t.label}` : props.labels?.[t.key] ?? t.key);
</script>

<style scoped>
.more { font-size: 12.5px; color: var(--c-primary-600); cursor: pointer; padding: 4px 10px; }
</style>
