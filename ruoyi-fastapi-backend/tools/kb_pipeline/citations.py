"""
指南推荐 → 引用文献（程序解析，不调用模型）：
1. 参考文献块 → 按序号拆成单条（“1. 作者. 题名. 期刊 年;卷:页.”），序号必须连续递增，防止把年份、页码当序号；
2. 推荐条款所在块中的引用标记 → clause_citation；
   推荐（或连续的一组推荐）之后、到下一条推荐或下一个标题之前的理由段落中的标记 → rationale_text；
3. 每条引用按 DOI、再按题名相似度匹配题录（lit_record），匹配结果为 candidate，待人工确认。
"""

import re

import psycopg2

# <sup>[61]</sup>、<sup>(9,80,81)</sup>、<sup>12–15</sup>、[12]、[12, 15–17]、$^{12}$
_MARKER = re.compile(
    r'<sup>\s*[\[(]?([\d,\s\-–—]+)[\])]?\s*</sup>|\[(\d{1,3}(?:\s*[,\-–—]\s*\d{1,3})*)\]|\$\^\{([\d,\-–—\s]+)\}\$'
)
_ENTRY = re.compile(r'(?m)^\s*\[?(\d{1,3})[.\]]\s+')
_DOI = re.compile(r'10\.\d{4,9}/[^\s;,]+')
TITLE_SIMILARITY = 0.75
RATIONALE_MAX_BLOCKS = 8
MAX_RANGE = 50  # 区间写法超过此跨度视为解析错误，只取起点
MIN_TITLE_CHARS = 15


def marker_numbers(text: str) -> list[int]:
    """
    文本中的引用序号（展开 12–15 这类区间），按出现顺序去重
    """
    nums: list[int] = []
    for m in _MARKER.finditer(text or ''):
        body = next(g for g in m.groups() if g)
        for part in re.split(r'\s*,\s*', body.strip()):
            bounds = re.split(r'\s*[\-–—]\s*', part)
            if not all(b.isdigit() for b in bounds if b):
                continue
            lo, hi = int(bounds[0]), int(bounds[-1] or bounds[0])
            for n in range(lo, (hi if 0 <= hi - lo <= MAX_RANGE else lo) + 1):
                if n not in nums:
                    nums.append(n)
    return nums


def reference_entries(texts: list[str]) -> dict[int, str]:
    """
    参考文献块文本（按阅读顺序）→ {序号: 该条全文}；跨块、跨页的续行并入上一条
    """
    joined = '\n'.join(texts)
    entries: dict[int, str] = {}
    hits = list(_ENTRY.finditer(joined))
    expected = 1
    starts: list[tuple[int, int, int]] = []  # (序号, 开始, 内容开始)
    for m in hits:
        n = int(m.group(1))
        if n == expected:
            starts.append((n, m.start(), m.end()))
            expected += 1
    for i, (n, _, content_start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(joined)
        entries[n] = re.sub(r'\s+', ' ', joined[content_start:end]).strip()
    return entries


def title_candidates(entry: str) -> list[str]:
    """
    温哥华格式“作者. 题名. 期刊…”：取第 2、3 个句段作为题名候选
    """
    parts = [p.strip() for p in re.split(r'\.\s+', entry) if p.strip()]
    return [p for p in parts[1:3] if len(p) >= MIN_TITLE_CHARS]


def match_entry(cur: psycopg2.extensions.cursor, entry: str) -> tuple[int | None, int | None, str]:
    """
    :return: (题录 record_id, 有全文时的 kb_document.document_id, match_status)
    """
    record_id = None
    m = _DOI.search(re.sub(r'(10\.\d{4,9}/)\s+', r'\1', entry))
    if m:
        cur.execute('SELECT record_id FROM lit_record WHERE lower(doi) = %s LIMIT 1', (m.group(0).rstrip('.').lower(),))
        row = cur.fetchone()
        record_id = row[0] if row else None
    if record_id is None:
        for cand in title_candidates(entry):
            cur.execute(
                """SELECT record_id, similarity(lower(title), lower(%s)) AS s FROM lit_record
                   WHERE lower(title) %% lower(%s) ORDER BY s DESC LIMIT 1""",
                (cand, cand),
            )
            row = cur.fetchone()
            if row and row[1] >= TITLE_SIMILARITY:
                record_id = row[0]
                break
    if record_id is None:
        return None, None, 'unmatched'
    cur.execute('SELECT document_id FROM kb_document WHERE record_id = %s', (record_id,))
    row = cur.fetchone()
    return record_id, row[0] if row else None, 'candidate'


def resolve(cur: psycopg2.extensions.cursor, version_id: int, rec_blocks: dict[int, list[int]]) -> int:
    """
    为本文献版本的推荐写入 kb_rec_citation

    :param rec_blocks: {rec_id: 推荐条款所在块的 block_id 列表}
    :return: 写入条数
    """
    cur.execute(
        'SELECT block_id, seq, section, block_type, text FROM kb_block WHERE version_id = %s ORDER BY seq',
        (version_id,),
    )
    blocks = cur.fetchall()
    entries = reference_entries([t for _, _, sec, kind, t in blocks if sec == 'references' and kind != 'heading'])
    if not entries:
        return 0
    order = {bid: i for i, (bid, *_) in enumerate(blocks)}
    rec_pos = {rid: sorted(order[b] for b in bids if b in order) for rid, bids in rec_blocks.items()}
    rec_pos = {rid: pos for rid, pos in rec_pos.items() if pos}
    rec_block_set = {p for pos in rec_pos.values() for p in pos}
    # 连续出现的推荐（中间没有理由段落）为一组，共享其后的理由段落
    groups: list[list[int]] = []
    for rid in sorted(rec_pos, key=lambda r: rec_pos[r][0]):
        if groups:
            last_end = rec_pos[groups[-1][-1]][-1]
            between = range(last_end + 1, rec_pos[rid][0])
            if all(p in rec_block_set for p in between):
                groups[-1].append(rid)
                continue
        groups.append([rid])
    written = 0
    for group in groups:
        rationale: list[int] = []
        p = rec_pos[group[-1]][-1] + 1
        while p < len(blocks) and len(rationale) < RATIONALE_MAX_BLOCKS:
            if p in rec_block_set or blocks[p][3] == 'heading' or blocks[p][2] == 'references':
                break
            rationale.append(p)
            p += 1
        rationale_nums = [n for q in rationale for n in marker_numbers(blocks[q][4])]
        for rid in group:
            clause_nums = [n for q in rec_pos[rid] for n in marker_numbers(blocks[q][4])]
            cited = [(n, 'clause_citation') for n in clause_nums]
            cited += [(n, 'rationale_text') for n in dict.fromkeys(rationale_nums) if n not in clause_nums]
            for seq, (n, basis) in enumerate(cited, 1):
                entry = entries.get(n)
                record_id, document_id, status = match_entry(cur, entry) if entry else (None, None, 'unmatched')
                cur.execute(
                    """INSERT INTO kb_rec_citation(rec_id, seq, ref_label, matched_record_id, matched_document_id,
                                                   match_status, citation_basis)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        rid,
                        seq,
                        f'[{n}] {entry or "（参考文献列表中未找到该序号）"}'[:500],
                        record_id,
                        document_id,
                        status,
                        basis,
                    ),
                )
                written += 1
    return written
