<template>
  <el-autocomplete :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" :fetch-suggestions="fetch"
    :placeholder="placeholder" clearable :size="size" :trigger-on-focus="false" :debounce="250" @select="onSelect" @keyup.enter="go">
    <template #prefix><el-icon><Search /></el-icon></template>
    <template #default="{ item }">
      <div class="sug">
        <span class="sug-type">{{ item.type === "concept" ? label(CONCEPT_TYPE, item.sub) : "文献" }}</span>
        <span class="sug-term">{{ item.term }}</span>
        <span class="muted" v-if="item.type === 'record' && item.sub">{{ item.sub }}</span>
      </div>
    </template>
  </el-autocomplete>
</template>

<script setup>
import { suggest } from "@/api";
import { CONCEPT_TYPE, label } from "@/labels";
const props = defineProps({ modelValue: String, placeholder: String, size: { type: String, default: "default" } });
defineEmits(["update:modelValue"]);
const router = useRouter();
async function fetch(q, cb) {
  if (!q || !q.trim()) return cb([]);
  try { cb(await suggest(q.trim())); } catch { cb([]); }
}
function onSelect(item) {
  router.push(item.type === "concept" ? `/concept/${item.id}` : `/record/${item.id}`);
}
function go() {
  const q = (props.modelValue || "").trim();
  if (q) router.push({ path: "/search", query: { q } });
}
</script>

<style scoped>
.sug { display: flex; align-items: center; gap: 6px; overflow: hidden; }
.sug-term { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
</style>
