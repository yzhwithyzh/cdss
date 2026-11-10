<template>
  <div class="page">
    <div class="layout-3 two">
      <aside class="card sticky">
        <div class="facet-title">概念类型</div>
        <div class="facet-item" :class="{ active: !f.conceptType }" @click="setType(null)"><span>全部</span></div>
        <div v-for="t in types" :key="t.key" class="facet-item" :class="{ active: f.conceptType === t.key }" @click="setType(t.key)">
          <span><i class="dot" :style="{ background: CONCEPT_COLOR[t.key] }" />{{ label(CONCEPT_TYPE, t.key) }}</span><span class="muted">{{ t.n }}</span>
        </div>
        <div class="facet-title">范围</div>
        <el-checkbox v-model="f.includeUnused" @change="run(1)">包含未被证据引用的概念</el-checkbox>
      </aside>

      <section class="card">
        <div class="toolbar">
          <h3 class="section-title" style="margin: 0">概念库</h3>
          <span class="muted">中英文名称与别名统一归并，按被证据引用次数排序</span>
          <el-input v-model="f.q" placeholder="搜索概念名或别名" clearable style="width: 260px; margin-left: auto" @keyup.enter="run(1)" @clear="run(1)">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
        <el-table :data="rows" v-loading="loading" size="default" style="margin-top: 12px" @row-click="(r) => $router.push(`/concept/${r.conceptId}`)" row-class-name="clickable">
          <el-table-column label="概念" min-width="260">
            <template #default="{ row }">
              <div class="cname"><i class="dot" :style="{ background: CONCEPT_COLOR[row.conceptType] }" /><b>{{ row.nameZh || row.nameEn }}</b></div>
              <div class="muted" v-if="row.nameZh && row.nameEn">{{ row.nameEn }}</div>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="120"><template #default="{ row }">{{ label(CONCEPT_TYPE, row.conceptType) }}</template></el-table-column>
          <el-table-column label="别名" width="70" prop="aliases" align="right" />
          <el-table-column label="研究发现" width="90" prop="findings" align="right" />
          <el-table-column label="推荐意见" width="90" prop="recs" align="right" />
          <el-table-column label="引用总数" width="160">
            <template #default="{ row }"><div class="bar"><i :style="{ width: (row.n / maxN) * 100 + '%' }" /><span>{{ row.n }}</span></div></template>
          </el-table-column>
        </el-table>
        <el-pagination style="margin-top: 12px; justify-content: flex-end" background layout="total, prev, pager, next" :total="total"
          v-model:current-page="f.pageNum" :page-size="f.pageSize" @current-change="run()" />
      </section>
    </div>
  </div>
</template>

<script setup>
import { concepts } from "@/api";
import { CONCEPT_COLOR, CONCEPT_TYPE, label } from "@/labels";
const route = useRoute();
const f = reactive({ q: route.query.q || "", conceptType: route.query.type || null, includeUnused: false, pageNum: 1, pageSize: 30 });
const rows = ref([]); const total = ref(0); const types = ref([]); const loading = ref(false); const maxN = ref(1);
function setType(t) { f.conceptType = t; run(1); }
async function run(p) {
  if (p) f.pageNum = p;
  loading.value = true;
  try {
    const d = await concepts(f);
    rows.value = d.rows; total.value = d.total; types.value = d.facets.conceptType;
    if (f.pageNum === 1) maxN.value = Math.max(1, ...d.rows.map((r) => r.n));
  } finally { loading.value = false; }
}
onMounted(() => run());
</script>

<style scoped>
.layout-3.two { grid-template-columns: 240px minmax(0, 1fr); }
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; flex-shrink: 0; }
.cname { display: flex; align-items: center; }
.bar { display: flex; align-items: center; gap: 6px; }
.bar i { height: 8px; border-radius: 4px; background: linear-gradient(90deg, var(--c-primary-600), var(--c-accent)); min-width: 2px; }
.bar span { font-variant-numeric: tabular-nums; color: var(--c-text-2); font-size: 12.5px; }
:deep(.clickable) { cursor: pointer; }
@media (max-width: 1100px) { .layout-3.two { grid-template-columns: minmax(0, 1fr); } }
</style>
