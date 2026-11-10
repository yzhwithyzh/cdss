import axios from "axios";
import { ElMessage } from "element-plus";

export const API_BASE = "/api/kbweb/public";
const http = axios.create({ baseURL: API_BASE, timeout: 30000 });
http.interceptors.response.use(
  (res) => {
    if (res.data?.code !== 200) {
      ElMessage.error(res.data?.msg || "请求失败");
      return Promise.reject(res.data);
    }
    return res.data.data;
  },
  (err) => {
    ElMessage.error(err.message || "网络错误");
    return Promise.reject(err);
  }
);

const clean = (o) => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false));

export const stats = () => http.get("/stats");
export const questions = () => http.get("/questions");
export const suggest = (q) => http.get("/suggest", { params: { q } });
export const search = (params) => http.get("/search", { params: clean(params) });
export const getRecord = (id) => http.get(`/record/${id}`);
export const getFulltext = (id) => http.get(`/record/${id}/fulltext`);
export const concepts = (params) => http.get("/concepts", { params: clean(params) });
export const getConcept = (id) => http.get(`/concept/${id}`);
export const getQuestion = (code) => http.get(`/question/${code}`);
export const compare = (params) => http.get("/compare", { params: clean(params) });

export const pdfUrl = (id, page) => `${API_BASE}/record/${id}/pdf${page ? `#page=${page}` : ""}`;
export const imageUrl = (id, path) => `${API_BASE}/record/${id}/image?path=${encodeURIComponent(path)}`;

// 问答：SSE 流，逐个事件回调（step / tool_result / answer / citations / error / done）
export async function chatStream(question, history, onEvent, signal) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, history }), signal,
  });
  if (!res.ok || !res.body) throw new Error(`请求失败（${res.status}）`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}
