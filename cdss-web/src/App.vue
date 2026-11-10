<template>
  <header class="app-header">
    <div class="header-inner">
      <router-link to="/" class="logo" aria-label="首页">
        <Logo :size="36" />
        <span class="wordmark">
          <span class="logo-main">循证知识库</span>
          <span class="logo-sub">{{ topic.topicName || "临床决策支持" }} · Evidence KB</span>
        </span>
      </router-link>

      <div class="search-box" v-if="$route.name !== 'home'">
        <SearchInput v-model="kw" class="header-search" placeholder="检索文献、推荐意见、研究发现或概念" />
      </div>

      <nav class="nav" aria-label="主导航">
        <router-link to="/chat"><el-icon><ChatDotRound /></el-icon><span>智能问答</span></router-link>
        <router-link to="/search"><el-icon><Search /></el-icon><span>检索</span></router-link>
        <router-link to="/question"><el-icon><Aim /></el-icon><span>循证问题</span></router-link>
        <router-link to="/concepts"><el-icon><CollectionTag /></el-icon><span>概念库</span></router-link>
        <router-link to="/compare"><el-icon><DataAnalysis /></el-icon><span>指南对比</span></router-link>
      </nav>
    </div>
  </header>

  <main class="app-main">
    <router-view :key="$route.path" />
  </main>

  <footer class="footer">
    <div class="footer-inner">
      <div class="footer-brand"><Logo :size="20" /><span>循证知识库 · {{ topic.topicName }}</span></div>
      <div class="footer-note">题录来自 PubMed、Embase、CNKI、万方、维普；结构化内容由模型抽取并经原文定位校验，尚未经临床专家审核，仅供专业人员参考。</div>
    </div>
  </footer>
</template>

<script setup>
import Logo from "@/components/Logo.vue";
import SearchInput from "@/components/SearchInput.vue";
import { topic, loadTopic } from "@/store";
const route = useRoute();
const kw = ref(route.query.q || "");
watch(() => route.query.q, (q) => { kw.value = q || ""; });
onMounted(loadTopic);
</script>

<style scoped>
.app-header { position: sticky; top: 0; z-index: 100; background: rgba(255, 255, 255, 0.92); backdrop-filter: saturate(160%) blur(10px); border-bottom: 1px solid var(--c-border); }
.header-inner { max-width: 1440px; margin: 0 auto; display: flex; align-items: center; gap: 28px; height: 64px; padding: 0 20px; }
.logo { display: flex; align-items: center; gap: 11px; flex-shrink: 0; }
.wordmark { display: flex; flex-direction: column; line-height: 1.1; }
.logo-main { font-size: 19px; font-weight: 800; color: var(--c-primary); letter-spacing: 2px; font-family: var(--font-serif); }
.logo-sub { color: var(--c-text-3); font-size: 11.5px; letter-spacing: 0.8px; margin-top: 4px; }
.search-box { flex: 1; max-width: 520px; }
.nav { margin-left: auto; display: flex; gap: 4px; }
.nav a { display: inline-flex; align-items: center; gap: 6px; color: var(--c-text-2); font-size: 14.5px; font-weight: 500; padding: 8px 12px; border-radius: 8px; position: relative; transition: color 0.15s var(--ease), background 0.15s var(--ease); }
.nav a:hover { color: var(--c-primary); background: var(--c-primary-50); }
.nav a.router-link-active { color: var(--c-primary); font-weight: 600; }
.nav a.router-link-active::after { content: ""; position: absolute; left: 12px; right: 12px; bottom: -13px; height: 2px; border-radius: 2px; background: var(--c-accent); }
.app-main { flex: 1; }
.footer { border-top: 1px solid var(--c-border); background: #fff; }
.footer-inner { max-width: 1440px; margin: 0 auto; padding: 20px; display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.footer-brand { display: flex; align-items: center; gap: 8px; font-weight: 600; color: var(--c-text-2); font-size: 13px; }
.footer-note { color: var(--c-text-3); font-size: 12.5px; max-width: 820px; }
@media (max-width: 900px) {
  .header-inner { gap: 12px; }
  .logo-sub, .nav a span { display: none; }
  .nav a { padding: 8px; }
}
</style>

<style>
.header-search .el-input__wrapper { border-radius: 999px; background: #f1f5f5; box-shadow: none; padding-left: 14px; }
.header-search .el-input__wrapper:hover { background: #eaf0f0; }
.header-search .el-input__wrapper.is-focus { background: #fff; box-shadow: 0 0 0 2px var(--c-primary-100); }
</style>
