import { reactive } from "vue";
import { stats } from "@/api";

// 首页统计在多处复用（页头主题名、首页图表），只请求一次
export const topic = reactive({ topicCode: "", topicName: "" });
let statsPromise = null;
export function loadStats() {
  statsPromise = statsPromise || stats().catch((e) => { statsPromise = null; throw e; });
  return statsPromise;
}
export async function loadTopic() {
  const s = await loadStats();
  Object.assign(topic, s.topic || {});
}
