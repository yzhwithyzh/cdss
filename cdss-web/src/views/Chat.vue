<template>
  <div class="page chat-page">
    <div class="layout-2">
      <section class="card conv">
        <div class="msgs" ref="box">
          <div v-if="!turns.length" class="welcome">
            <h2 class="page-title">循证问答</h2>
            <p class="muted">基于库内 4.2 万篇题录和 40 篇结构化全文回答，每条结论标注出处。随访间隔类问题会分别给出各指南建议，库外指南条款标注【待原文核对】。</p>
            <div class="examples">
              <button v-for="e in EXAMPLES" :key="e.q" type="button" class="ex" @click="ask(e.q)">
                <span class="ex-tag">{{ e.tag }}</span>{{ e.q }}
              </button>
            </div>
          </div>

          <template v-for="(t, i) in turns" :key="i">
            <div class="msg user"><div class="bubble">{{ t.question }}</div></div>
            <div class="msg bot">
              <div class="bubble">
                <div class="steps" v-if="t.steps.length">
                  <button type="button" class="steps-toggle" @click="t.showSteps = !t.showSteps">
                    <el-icon v-if="t.loading" class="is-loading"><Loading /></el-icon><el-icon v-else><Finished /></el-icon>
                    {{ t.loading ? "正在" + (t.steps.at(-1)?.label || "思考") : `检索过程 ${t.steps.length} 步 · ${t.seconds}s` }}
                    <el-icon class="caret" :class="{ open: t.showSteps }"><ArrowDown /></el-icon>
                  </button>
                  <ol v-if="t.showSteps || t.loading" class="step-list">
                    <li v-for="(s, j) in t.steps" :key="j" :class="{ err: s.error }">
                      <span class="s-label">{{ s.label }}</span>
                      <span class="s-detail">{{ s.detail }}</span>
                      <span class="s-res" v-if="s.done">{{ s.error ? "出错" : s.hits ? `${s.hits} 篇` : "✓" }}</span>
                    </li>
                  </ol>
                </div>
                <div v-if="t.answer" class="answer" v-html="render(t)" @click="onCiteClick($event, t)" />
                <div v-else-if="t.loading && !t.steps.length" class="muted">正在理解问题…</div>
                <el-alert v-if="t.error" type="error" :closable="false" :title="t.error" />
                <div class="cite-bar" v-if="t.citations.length">
                  <span class="muted">引用 {{ t.citations.length }} 篇</span>
                  <span v-if="badCites(t)" class="bad">其中 {{ badCites(t) }} 篇未出现在检索结果中，请核实</span>
                  <a href="#" @click.prevent="active = i">查看引用 ›</a>
                </div>
              </div>
            </div>
          </template>
        </div>

        <div class="composer">
          <el-input v-model="input" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" resize="none"
            placeholder="描述患者情况或提出证据问题，Enter 发送，Shift+Enter 换行" @keydown.enter.exact.prevent="ask()" />
          <div class="actions">
            <el-button v-if="busy" @click="stop"><el-icon><VideoPause /></el-icon>停止</el-button>
            <el-button v-else-if="turns.length" text @click="reset">新对话</el-button>
            <el-button type="primary" :disabled="busy || !input.trim()" @click="ask()"><el-icon><Promotion /></el-icon>发送</el-button>
          </div>
        </div>
      </section>

      <aside class="card side sticky">
        <h3 class="section-title">引用文献<span class="extra" v-if="activeTurn">第 {{ active + 1 }} 轮</span></h3>
        <template v-if="activeTurn?.citations.length">
          <div v-for="c in activeTurn.citations" :key="c.sourceId" :id="'cite-' + c.sourceId" class="cite" :class="{ bad: !c.retrieved || !c.exists, hl: hl === c.sourceId }">
            <div class="cite-id">【{{ c.sourceId }}】
              <el-tag v-if="c.structured" size="small" effect="dark">已结构化</el-tag>
              <el-tag v-if="!c.exists" size="small" type="danger">库内不存在</el-tag>
              <el-tag v-else-if="!c.retrieved" size="small" type="warning">未经检索</el-tag>
            </div>
            <router-link v-if="c.recordId" :to="`/record/${c.recordId}`" target="_blank" class="cite-title">{{ c.title }}</router-link>
            <div class="muted">{{ c.pubYear }}<template v-if="c.designLabel"> · {{ c.designLabel }}</template></div>
          </div>
        </template>
        <p v-else class="muted">回答中引用的文献会列在这里，点击可打开详情。</p>
        <div class="note muted">
          回答由大模型基于检索结果生成，尚未经临床审核。USMSTF / ESGE / BSG 2020 原文不在库内，相关条款标【待原文核对】。
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { chatStream } from "@/api";
import { esc } from "@/labels";
import { renderMarkdown } from "@/md";

const EXAMPLES = [
  { tag: "随访间隔", q: "62岁男性，首次筛查肠镜切除2枚管状腺瘤，最大6 mm，低级别异型增生，肠道准备充分、完整切除。下次肠镜什么时候做？" },
  { tag: "证据", q: "≥20 mm 病变分片 EMR 后局部复发率是多少？哪些因素与复发有关？" },
  { tag: "指南比较", q: "切除3枚管状腺瘤，都小于10 mm、低级别异型增生。美国和欧洲指南的随访建议一样吗？" },
  { tag: "适用性", q: "内镜下估计息肉10 mm，病理测量8 mm，按哪个分组？" },
  { tag: "锯齿状病变", q: "无蒂锯齿状病变伴异型增生，切除后的随访有什么证据？" },
];

const turns = ref([]); const input = ref(""); const busy = ref(false); const active = ref(-1); const hl = ref(null);
const box = ref(); let ctrl = null;
const activeTurn = computed(() => turns.value[active.value]);
const badCites = (t) => t.citations.filter((c) => !c.retrieved || !c.exists).length;

function render(t) {
  const by = Object.fromEntries(t.citations.map((c) => [c.sourceId, c]));
  return renderMarkdown(t.answer, (id) => {
    const c = by[id];
    const cls = !c ? "cite-ref" : c.exists && c.retrieved ? "cite-ref ok" : "cite-ref bad";
    return `<a class="${cls}" data-sid="${id}" title="${esc(c?.title || "")}">${id}</a>`;
  });
}
function onCiteClick(e, t) {
  const sid = e.target?.dataset?.sid;
  if (!sid) return;
  active.value = turns.value.indexOf(t); hl.value = sid;
  nextTick(() => document.getElementById("cite-" + sid)?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
}
async function scrollDown() { await nextTick(); box.value?.scrollTo({ top: box.value.scrollHeight, behavior: "smooth" }); }

async function ask(q) {
  const question = (q ?? input.value).trim();
  if (!question || busy.value) return;
  input.value = "";
  const history = turns.value.filter((t) => t.answer).flatMap((t) => [{ role: "user", content: t.question }, { role: "assistant", content: t.answer }]);
  const t = reactive({ question, steps: [], answer: "", citations: [], loading: true, showSteps: false, seconds: 0, error: "" });
  turns.value.push(t); active.value = turns.value.length - 1; busy.value = true;
  scrollDown();
  ctrl = new AbortController();
  try {
    await chatStream(question, history, (ev) => {
      if (ev.type === "step") t.steps.push({ label: ev.label, detail: ev.detail, tool: ev.tool });
      else if (ev.type === "tool_result") {
        const s = [...t.steps].reverse().find((x) => x.tool === ev.tool && !x.done);
        if (s) Object.assign(s, { done: true, hits: ev.hits, error: ev.error });
      } else if (ev.type === "answer") { t.answer = ev.content; t.seconds = ev.t; }
      else if (ev.type === "citations") t.citations = ev.items.map((c) => ({ ...c, sourceId: c.sourceId ?? c.source_id }));
      else if (ev.type === "error") t.error = "回答失败：" + ev.message;
      scrollDown();
    }, ctrl.signal);
  } catch (e) {
    if (e.name !== "AbortError") t.error = "回答失败：" + e.message;
    else t.error = "已停止";
  } finally {
    t.loading = false; busy.value = false; ctrl = null;
  }
}
function stop() { ctrl?.abort(); }
function reset() { turns.value = []; active.value = -1; }
onBeforeUnmount(stop);
</script>

<style scoped>
.chat-page .layout-2 { grid-template-columns: minmax(0, 1fr) 340px; }
.conv { display: flex; flex-direction: column; height: calc(100vh - 120px); min-height: 560px; padding: 0; }
.msgs { flex: 1; overflow: auto; padding: 20px 24px; }
.welcome { max-width: 760px; margin: 40px auto 0; text-align: center; }
.welcome p { margin: 10px 0 24px; line-height: 1.8; }
.examples { display: grid; gap: 10px; text-align: left; }
.ex { border: 1px solid var(--c-border); background: #fff; border-radius: var(--radius-sm); padding: 10px 14px; text-align: left; font: inherit; font-size: 13.5px; color: var(--c-text); cursor: pointer; line-height: 1.6; transition: border-color 0.15s var(--ease), background 0.15s var(--ease); }
.ex:hover { border-color: var(--c-primary-600); background: var(--c-primary-50); }
.ex-tag { font-size: 11.5px; color: #7a4614; background: var(--c-accent-50); border: 1px solid var(--c-accent-100); border-radius: 4px; padding: 0 6px; margin-right: 8px; }
.msg { display: flex; margin-bottom: 18px; }
.msg.user { justify-content: flex-end; }
.msg.user .bubble { background: var(--c-primary); color: #fff; border-radius: 12px 12px 2px 12px; padding: 10px 14px; max-width: 75%; line-height: 1.7; white-space: pre-wrap; }
.msg.bot .bubble { background: #fff; border: 1px solid var(--c-border); border-radius: 12px 12px 12px 2px; padding: 12px 16px; max-width: 92%; min-width: 280px; }
.steps { margin-bottom: 8px; }
.steps-toggle { display: inline-flex; align-items: center; gap: 6px; border: 0; background: var(--c-primary-50); color: var(--c-primary); border-radius: 999px; padding: 3px 12px; font: inherit; font-size: 12.5px; cursor: pointer; }
.caret { transition: transform 0.15s var(--ease); }
.caret.open { transform: rotate(180deg); }
.step-list { margin: 8px 0 4px; padding-left: 22px; font-size: 12.5px; color: var(--c-text-2); line-height: 1.8; }
.step-list li.err { color: var(--c-danger); }
.s-label { font-weight: 600; margin-right: 6px; }
.s-detail { color: var(--c-text-3); }
.s-res { margin-left: 6px; color: var(--c-success); }
.answer { font-size: 14.5px; line-height: 1.85; color: var(--c-text); }
.answer :deep(h4), .answer :deep(h5) { font-size: 15px; margin: 12px 0 4px; }
.answer :deep(p) { margin: 6px 0; }
.answer :deep(ul), .answer :deep(ol) { margin: 4px 0; padding-left: 22px; }
.answer :deep(table) { border-collapse: collapse; font-size: 13px; margin: 8px 0; width: 100%; }
.answer :deep(th), .answer :deep(td) { border: 1px solid var(--c-border-strong); padding: 4px 8px; vertical-align: top; }
.answer :deep(th) { background: #f6f9f9; }
.answer :deep(.verify) { color: #9a5a1c; font-size: 12.5px; }
.answer :deep(.cite-ref) { display: inline-block; font-size: 11.5px; line-height: 16px; padding: 0 4px; margin: 0 1px; border-radius: 3px; background: var(--c-primary-50); color: var(--c-primary); cursor: pointer; vertical-align: 1px; font-family: var(--font-mono); }
.answer :deep(.cite-ref.bad) { background: #fdecea; color: var(--c-danger); }
.cite-bar { display: flex; gap: 12px; align-items: center; margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--c-border); font-size: 12.5px; }
.cite-bar .bad { color: var(--c-danger); }
.composer { border-top: 1px solid var(--c-border); padding: 12px 16px; display: flex; gap: 10px; align-items: flex-end; }
.composer .el-textarea { flex: 1; }
.actions { display: flex; gap: 6px; }
.side { max-height: calc(100vh - 120px); }
.cite { padding: 8px 10px; border-radius: 6px; border: 1px solid transparent; margin-bottom: 6px; font-size: 13px; line-height: 1.5; transition: background 0.3s var(--ease); }
.cite.hl { background: var(--c-accent-50); border-color: var(--c-accent-100); }
.cite.bad { border-color: #f3c7c2; }
.cite-id { font-family: var(--font-mono); font-size: 12px; color: var(--c-primary); display: flex; gap: 6px; align-items: center; }
.cite-title { color: var(--c-text); display: block; margin: 2px 0; }
.note { margin-top: 14px; font-size: 12px; line-height: 1.7; border-top: 1px dashed var(--c-border); padding-top: 10px; }
@media (max-width: 1100px) { .chat-page .layout-2 { grid-template-columns: minmax(0, 1fr); } .side { display: none; } }
</style>
