import { esc } from "@/labels";

// 问答回答用的极简 Markdown：标题、列表、表格、加粗、【source_id】引用。先整体转义再生成标签，不放行任何原始 HTML
function inline(s, cite) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/【待原文核对[^】]*】/g, (m) => `<span class="verify">${m}</span>`)
    .replace(/【(\d+)】/g, (m, id) => cite(id));
}

export function renderMarkdown(src, cite = (id) => `【${id}】`) {
  const lines = esc(src || "").split(/\r?\n/);
  const out = [];
  let list = null, table = null;
  const flush = () => {
    if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${i}</li>`).join("")}</${list.tag}>`); list = null; }
    if (table) {
      const [head, ...body] = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r.join("|")));
      out.push(`<table><thead><tr>${head.map((c) => `<th>${inline(c, cite)}</th>`).join("")}</tr></thead><tbody>${body
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c, cite)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      table = null;
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (list) flush();
      (table = table || []).push(line.trim().slice(1, -1).split("|").map((c) => c.trim()));
      continue;
    }
    const li = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.*)$/);
    if (li) {
      if (table) flush();
      const tag = li[1] ? "ol" : "ul";
      if (list && list.tag !== tag) flush();
      (list = list || { tag, items: [] }).items.push(inline(li[2], cite));
      continue;
    }
    flush();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) out.push(`<h${Math.min(6, h[1].length + 3)}>${inline(h[2], cite)}</h${Math.min(6, h[1].length + 3)}>`);
    else if (line.trim()) out.push(`<p>${inline(line, cite)}</p>`);
  }
  flush();
  return out.join("");
}
