<template>
  <div class="home cdss-home">
    <header class="masthead reveal">
      <div class="masthead-main">
        <p class="eyebrow">CLINICAL DECISION SUPPORT · 证据驱动的临床辅助</p>
        <h1 class="greeting">
          {{ greeting }}，<span class="name">{{ displayName }}</span>
        </h1>
        <p class="today">{{ today }}</p>
      </div>
      <blockquote class="principle">
        <span class="principle-mark">原则</span>
        系统提供候选判断及其原文依据；最终诊断、处方与医嘱由具备相应职责的医务人员确认。
      </blockquote>
    </header>

    <section
      class="block reveal"
      style="--delay: 80ms"
    >
      <div class="block-head">
        <span class="block-index">01</span>
        <h2 class="block-title">证据处理路径</h2>
        <span class="block-note">每篇文献从登记到进入临床检索所经过的步骤</span>
      </div>
      <ol class="pathway">
        <li
          v-for="(step, i) in pathway"
          :key="step.name"
          class="step"
          :class="`is-${step.status}`"
          :style="{ '--i': i }"
        >
          <span class="step-dot"></span>
          <span class="step-no">{{ String(i + 1).padStart(2, '0') }}</span>
          <h3 class="step-name">{{ step.name }}</h3>
          <p class="step-desc">{{ step.desc }}</p>
          <span class="step-status">{{ statusText[step.status] }}</span>
        </li>
      </ol>
    </section>

    <div class="split">
      <section
        class="block reveal"
        style="--delay: 160ms"
      >
        <div class="block-head">
          <span class="block-index">02</span>
          <h2 class="block-title">病种专题</h2>
        </div>
        <ul class="topics">
          <li
            v-for="topic in topics"
            :key="topic.name"
            class="topic"
            :class="`is-${topic.status}`"
          >
            <div class="topic-head">
              <h3 class="topic-name">{{ topic.name }}</h3>
              <span class="topic-status">{{ statusText[topic.status] }}</span>
            </div>
            <p class="topic-desc">{{ topic.desc }}</p>
          </li>
          <li class="topic is-placeholder">
            <p class="topic-desc">其他病种按同一流程接入，共用文献处理、质量评价与审核机制。</p>
          </li>
        </ul>
      </section>

      <section
        class="block reveal"
        style="--delay: 240ms"
      >
        <div class="block-head">
          <span class="block-index">03</span>
          <h2 class="block-title">常用入口</h2>
        </div>
        <ul
          v-if="shortcuts.length"
          class="shortcuts"
        >
          <li
            v-for="item in shortcuts"
            :key="item.path"
          >
            <router-link
              :to="item.path"
              class="shortcut"
            >
              <span class="shortcut-name">{{ item.name }}</span>
              <span class="shortcut-desc">{{ item.desc }}</span>
              <span class="shortcut-arrow">→</span>
            </router-link>
          </li>
        </ul>
        <p
          v-else
          class="empty"
        >
          当前账号暂无可用的管理入口。
        </p>
      </section>
    </div>
  </div>
</template>

<script setup>
import auth from '@/plugins/auth'
import useUserStore from '@/store/modules/user'

const userStore = useUserStore()

const displayName = computed(() => userStore.nickName || userStore.name || '医生')

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 13) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const today = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
  weekday: 'long',
}).format(new Date())

const statusText = {
  active: '当前重点',
  pending: '待导入',
  planned: '规划中',
}

const pathway = [
  { name: '文献登记', desc: '题录与全文入库，文件哈希去重', status: 'pending' },
  { name: '研究类型分类', desc: '识别研究设计及其可支持的临床问题', status: 'active' },
  { name: '质量评价', desc: '按研究类型选用对应的偏倚风险工具', status: 'planned' },
  { name: '数据抽取', desc: '结构化抽取结果，保留原文定位', status: 'planned' },
  { name: '审核发布', desc: '人工核验后形成带版本的知识', status: 'planned' },
  { name: '临床问答', desc: '结合病例条件检索并解释证据', status: 'planned' },
]

const topics = [
  {
    name: '结直肠腺瘤',
    desc: '首个专病。题录与全文已收集，正在建立研究类型分类流程。',
    status: 'active',
  },
  {
    name: '产后出血',
    desc: '后续接入，沿用同一套文献处理与评价流程。',
    status: 'planned',
  },
]

const allShortcuts = [
  { name: '用户管理', desc: '医生与审核人员账号', path: '/system/user', perm: 'system:user:list' },
  { name: '角色管理', desc: '角色与数据权限', path: '/system/role', perm: 'system:role:list' },
  {
    name: '字典管理',
    desc: '研究类型、评价工具等枚举',
    path: '/system/dict',
    perm: 'system:dict:list',
  },
  { name: '文件管理', desc: '文献全文与附件', path: '/system/file', perm: 'system:file:list' },
  {
    name: '插件管理',
    desc: '启用 AI 等功能模块',
    path: '/system/plugin',
    perm: 'system:plugin:list',
  },
  { name: '定时任务', desc: '批处理任务调度', path: '/monitor/job', perm: 'monitor:job:list' },
]

const shortcuts = computed(() => allShortcuts.filter((item) => auth.hasPermi(item.perm)))
</script>

<style scoped lang="scss">
.home {
  --ink: #16323a;
  --ink-soft: #5b6e73;
  --ink-faint: #93a3a6;
  --teal: #0e7c86;
  --teal-wash: rgba(14, 124, 134, 0.07);
  --amber: #e0951c;
  --amber-wash: rgba(245, 166, 35, 0.12);
  --amber-ink: #8a5a0a;
  --paper: #f7f5f0;
  --card: #fffdf9;
  --rule: #e4dfd4;
  --serif: 'Noto Serif SC', 'Source Han Serif SC', 'Songti SC', 'STSong', 'SimSun', serif;

  min-height: calc(100vh - 84px);
  padding: 36px 44px 48px;
  color: var(--ink);
  background:
    radial-gradient(circle at 92% -10%, rgba(14, 124, 134, 0.08), transparent 42%),
    linear-gradient(var(--paper), var(--paper));
}

/* ---------- masthead ---------- */
.masthead {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 380px);
  gap: 40px;
  align-items: end;
  padding-bottom: 28px;
  border-bottom: 1px solid var(--rule);
}

.eyebrow {
  margin: 0 0 14px;
  font-size: 12px;
  letter-spacing: 0.18em;
  color: var(--teal);
}

.greeting {
  margin: 0;
  font-family: var(--serif);
  font-size: 34px;
  font-weight: 600;
  line-height: 1.25;
  letter-spacing: 0.02em;

  .name {
    color: var(--teal);
  }
}

.today {
  margin: 10px 0 0;
  font-size: 14px;
  color: var(--ink-soft);
}

.principle {
  position: relative;
  margin: 0;
  padding: 16px 18px 16px 20px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-soft);
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--amber);
  border-radius: 2px 8px 8px 2px;
}

.principle-mark {
  display: block;
  margin-bottom: 4px;
  font-family: var(--serif);
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

/* ---------- blocks ---------- */
.block {
  margin-top: 36px;
}

.block-head {
  display: flex;
  gap: 12px;
  align-items: baseline;
  margin-bottom: 18px;
}

.block-index {
  font-family: var(--serif);
  font-size: 13px;
  color: var(--amber);
}

.block-title {
  margin: 0;
  font-family: var(--serif);
  font-size: 20px;
  font-weight: 600;
}

.block-note {
  font-size: 13px;
  color: var(--ink-faint);
}

/* ---------- pathway ---------- */
.pathway {
  position: relative;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0;
  padding: 0;
  margin: 0;
  list-style: none;

  &::before {
    position: absolute;
    top: 7px;
    right: calc(100% / 12);
    left: calc(100% / 12);
    height: 1px;
    content: '';
    background: var(--rule);
  }
}

.step {
  position: relative;
  padding: 0 14px;
  text-align: center;
}

.step-dot {
  position: relative;
  z-index: 1;
  display: block;
  width: 15px;
  height: 15px;
  margin: 0 auto 16px;
  background: var(--paper);
  border: 1.5px solid var(--ink-faint);
  border-radius: 50%;
}

.step-no {
  font-family: var(--serif);
  font-size: 12px;
  color: var(--ink-faint);
}

.step-name {
  margin: 4px 0 6px;
  font-family: var(--serif);
  font-size: 16px;
  font-weight: 600;
}

.step-desc {
  min-height: 40px;
  text-wrap: balance;
  margin: 0 0 10px;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--ink-soft);
}

.step-status,
.topic-status {
  display: inline-block;
  padding: 2px 10px;
  font-size: 12px;
  color: var(--ink-faint);
  border: 1px solid var(--rule);
  border-radius: 999px;
}

.step.is-active {
  .step-dot {
    background: var(--amber);
    border-color: var(--amber);
    box-shadow: 0 0 0 5px var(--amber-wash);
    animation: pulse 2.6s ease-in-out infinite;
  }

  .step-no {
    color: var(--amber);
  }
}

.step.is-pending .step-dot {
  border-color: var(--teal);
}

.is-active > .step-status,
.is-active .topic-status {
  color: var(--amber-ink);
  background: var(--amber-wash);
  border-color: transparent;
}

.is-pending > .step-status {
  color: var(--teal);
  background: var(--teal-wash);
  border-color: transparent;
}

/* ---------- split ---------- */
.split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 40px;
}

/* ---------- topics ---------- */
.topics {
  display: grid;
  gap: 12px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.topic {
  padding: 18px 20px;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 8px;
  transition:
    border-color 0.2s ease,
    transform 0.2s ease;

  &:hover {
    border-color: var(--teal);
    transform: translateY(-1px);
  }

  &.is-active {
    border-left: 3px solid var(--teal);
  }

  &.is-placeholder {
    background: transparent;
    border-style: dashed;

    &:hover {
      border-color: var(--rule);
      transform: none;
    }
  }
}

.topic-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.topic-name {
  margin: 0;
  font-family: var(--serif);
  font-size: 17px;
  font-weight: 600;
}

.topic-desc {
  margin: 8px 0 0;
  text-wrap: pretty;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-soft);

  .is-placeholder & {
    margin: 0;
    color: var(--ink-faint);
  }
}

/* ---------- shortcuts ---------- */
.shortcuts {
  padding: 0;
  margin: 0;
  list-style: none;
  border-top: 1px solid var(--rule);
}

.shortcut {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 14px 6px;
  color: inherit;
  text-decoration: none;
  border-bottom: 1px solid var(--rule);
  transition:
    background 0.2s ease,
    padding 0.2s ease;

  &:hover {
    padding-left: 12px;
    background: var(--teal-wash);

    .shortcut-arrow {
      color: var(--teal);
      transform: translateX(3px);
    }
  }
}

.shortcut-name {
  font-family: var(--serif);
  font-size: 15px;
  font-weight: 600;
}

.shortcut-desc {
  font-size: 13px;
  color: var(--ink-soft);
}

.shortcut-arrow {
  color: var(--ink-faint);
  transition:
    transform 0.2s ease,
    color 0.2s ease;
}

.empty {
  font-size: 13px;
  color: var(--ink-faint);
}

/* ---------- motion ---------- */
.reveal {
  animation: rise 0.6s cubic-bezier(0.2, 0.7, 0.2, 1) both;
  animation-delay: var(--delay, 0ms);
}

.step {
  animation: rise 0.5s cubic-bezier(0.2, 0.7, 0.2, 1) both;
  animation-delay: calc(140ms + var(--i) * 60ms);
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes pulse {
  0%,
  100% {
    box-shadow: 0 0 0 4px var(--amber-wash);
  }

  50% {
    box-shadow: 0 0 0 8px var(--amber-wash);
  }
}

@media (prefers-reduced-motion: reduce) {
  .reveal,
  .step,
  .step.is-active .step-dot {
    animation: none;
  }
}

/* ---------- responsive ---------- */
@media (max-width: 1280px) {
  .masthead {
    grid-template-columns: 1fr;
    gap: 20px;
  }

  .principle {
    max-width: 640px;
  }
}

@media (max-width: 1100px) {
  .pathway {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    row-gap: 28px;

    &::before {
      display: none;
    }
  }
}

@media (max-width: 900px) {
  .home {
    padding: 24px 20px 36px;
  }

  .split {
    grid-template-columns: 1fr;
    gap: 24px;
  }
}

@media (max-width: 560px) {
  .pathway {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

<style lang="scss">
/* 暗色变量放在非 scoped 块中：scoped 的 :global() 会丢弃后续选择器 */
html.dark .cdss-home {
  --ink: #e6eeee;
  --ink-soft: #a2b3b5;
  --ink-faint: #6d7f82;
  --teal: #3fb3bc;
  --teal-wash: rgba(63, 179, 188, 0.1);
  --amber: #f2b04a;
  --amber-wash: rgba(242, 176, 74, 0.12);
  --amber-ink: #f2b04a;
  --paper: #121a1c;
  --card: #172124;
  --rule: #263437;
}
</style>
