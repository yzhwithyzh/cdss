"""
把解析结果（MinerU content_list / PMC JATS XML）转换为 kb_block 行

规则（来自第0轮解析抽查）：
1. 页眉、页脚、页码、侧栏文字过滤；
2. 跨栏/跨页段落：MinerU 把文字并入起始块、续接处留空块 → 空块坐标并入前一块，记录结束页；
3. 坐标为显示方向 0–1000，另存页面旋转角，供原文高亮时反旋转；
4. 表格 HTML 中的实体（&lt; 等）还原后转为“单元格 | 分隔”的纯文本，原始 HTML 存 extra；
5. 页码从 1 开始。
"""

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

SKIP_TYPES = {'header', 'footer', 'page_number', 'aside_text'}
# 标题前的编号：1 / 1.2 / 2.3.1 / 一、/（一）/ I. / A)
_NUM = r'^\s*(?:\d+(?:\.\d+)*[.、\s]*|[一二三四五六七八九十]+[、.]\s*|[（(][一二三四五六七八九十\d]+[）)]\s*|[IVX]+\.\s*|[A-Z]\)\s*)?'
SECTION_RULES = [
    ('references', _NUM + r'(references?|bibliography|literatur|参\s*考\s*文\s*献)'),
    # 入库时舍弃：致谢、基金、利益冲突、作者信息
    (
        'backmatter',
        _NUM + r'(acknowledge?ments?|funding|financial support|conflicts? of interest|competing interests?|'
        r'disclosures?|declarations?|authors?[’\'‘]?\s*contributions?|author details|author information|'
        r'institutions|academic (advisor|supervisors?)|致\s*谢|基金|利益冲突|作者贡献|通信作者|通讯作者)',
    ),
    ('supplement', _NUM + r'(appendix|supplementary|附\s*录)'),
    ('abstract', _NUM + r'(abstract|summary|摘\s*要|【?摘要】?)'),
    ('introduction', _NUM + r'(introduction|background|引\s*言|前\s*言|背景)'),
    (
        'methods',
        _NUM + r'(methods?|materials?|patients and methods|study design|subjects|'
        r'(对象|资料|材料)[与和及]方法|方法)',
    ),
    ('results', _NUM + r'(results?|结\s*果)'),
    ('discussion', _NUM + r'(discussion|讨\s*论)'),
    ('conclusion', _NUM + r'(conclusions?|结\s*论|小\s*结|总\s*结)'),
]


@dataclass
class Block:
    """
    待写入 kb_block 的一行
    """

    block_key: str
    seq: int
    page: int | None
    section: str
    block_type: str
    text: str
    regions: list[dict] = field(default_factory=list)
    page_end: int | None = None
    table_ref: str | None = None
    extra: dict | None = None
    heading_level: int | None = None  # 仅标题块；0=文献题名

    def bbox_json(self, rotation: int = 0) -> str | None:
        return json.dumps({'rotation': rotation, 'regions': self.regions}) if self.regions else None


def classify_heading(text: str, current: str, is_first_heading: bool = False) -> str:
    """
    按标题判定章节；匹配不上时沿用当前章节（小节归属上级章节）。文档标题之后不再有 title 段：
    综述、指南等非 IMRaD 文章的自定义小节归为 other（正文）。
    致谢/基金等（backmatter）与参考文献之后出现的未识别标题是新的正文小节，不再沿用，归为 other
    """
    t = text.strip()
    for section, pattern in SECTION_RULES:
        if re.match(pattern, t, re.IGNORECASE):
            return section
    if current in ('backmatter', 'references') or (current == 'title' and not is_first_heading):
        return 'other'
    return current


def table_text(table_html: str) -> str:
    """
    HTML 表格 → 每行“单元格 | 单元格”，实体还原
    """
    rows = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table_html or '', re.S):
        cells = [
            html.unescape(re.sub(r'<[^>]+>', ' ', c)).strip()
            for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)
        ]
        rows.append(' | '.join(re.sub(r'\s+', ' ', c) for c in cells))
    return '\n'.join(rows)


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value) if isinstance(value, str) and value.startswith('[') else value
    except ValueError:
        parsed = value
    return [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]


def _bbox(value: object) -> list[float]:
    box = json.loads(value) if isinstance(value, str) else value
    return [float(v) for v in box]


def blocks_from_mineru(content_list: list[dict]) -> list[Block]:  # noqa: PLR0912  按块类型分派
    """
    MinerU content_list.json → Block 列表
    """
    blocks: list[Block] = []
    section = 'title'
    last_text: Block | None = None
    for raw in content_list:
        kind = raw['type']
        if kind in SKIP_TYPES:
            continue
        page = int(raw['page_idx']) + 1
        region = {'page': page, 'box': _bbox(raw['bbox'])}
        if kind == 'text':
            text = (raw.get('text') or '').strip()
            if not text:  # 续接占位：坐标并入前一段
                if last_text is not None:
                    last_text.regions.append(region)
                    last_text.page_end = max(last_text.page_end or last_text.page, page)
                continue
            if raw.get('text_level'):
                section = classify_heading(
                    text, section, is_first_heading=not any(b.block_type == 'heading' for b in blocks)
                )
                block_type = 'heading'
            else:
                block_type = 'paragraph'
            blk = Block(f'p{page}-{len(blocks)}', len(blocks), page, section, block_type, text, [region], page)
            if block_type == 'heading':
                blk.heading_level = int(raw['text_level'])
            blocks.append(blk)
            last_text = blk if block_type == 'paragraph' else None
            continue
        last_text = None
        if kind == 'table':
            caption = ' '.join(_as_list(raw.get('table_caption')))
            footnote = ' '.join(_as_list(raw.get('table_footnote')))
            body = table_text(raw.get('table_body') or '')
            text = '\n'.join(x for x in (caption, body, footnote) if x)
            label = re.match(r'[▶\s]*(表\s*\d+|Tab(?:le)?\.?\s*\d+)', caption, re.IGNORECASE)
            blocks.append(
                Block(
                    f'p{page}-{len(blocks)}',
                    len(blocks),
                    page,
                    section,
                    'table',
                    text or '(空表格)',
                    [region],
                    page,
                    label.group(1) if label else None,
                    {
                        'html': raw.get('table_body'),
                        'caption': caption,
                        'footnote': footnote,
                        'img_path': raw.get('img_path'),
                    },
                )
            )
        elif kind in ('image', 'chart'):
            caption = ' '.join(_as_list(raw.get('image_caption') or raw.get('chart_caption')))
            footnote = ' '.join(_as_list(raw.get('image_footnote') or raw.get('chart_footnote')))
            if caption or footnote:
                blocks.append(
                    Block(
                        f'p{page}-{len(blocks)}',
                        len(blocks),
                        page,
                        section,
                        'caption',
                        '\n'.join(x for x in (caption, footnote) if x),
                        [region],
                        page,
                        extra={'kind': kind, 'img_path': raw.get('img_path')},
                    )
                )
        elif kind == 'list':
            items = _as_list(raw.get('list_items'))
            is_ref = raw.get('sub_type') == 'ref_text'
            blocks.append(
                Block(
                    f'p{page}-{len(blocks)}',
                    len(blocks),
                    page,
                    'references' if is_ref else section,
                    'list_item',
                    '\n'.join(items) or '(空列表)',
                    [region],
                    page,
                )
            )
        elif kind == 'equation':
            blocks.append(
                Block(
                    f'p{page}-{len(blocks)}',
                    len(blocks),
                    page,
                    section,
                    'other',
                    raw.get('text') or '(公式)',
                    [region],
                    page,
                    extra={'kind': 'equation'},
                )
            )
        elif kind == 'page_footnote':
            text = (raw.get('text') or '').strip()
            if text:
                blocks.append(
                    Block(
                        f'p{page}-{len(blocks)}',
                        len(blocks),
                        page,
                        'backmatter',
                        'other',
                        text,
                        [region],
                        page,
                        extra={'kind': 'page_footnote'},
                    )
                )
    return blocks


def _itertext(el: ET.Element) -> str:
    return re.sub(r'\s+', ' ', ''.join(el.itertext())).strip()


def blocks_from_jats(xml_path: Path) -> list[Block]:
    """
    PMC JATS XML → Block 列表（无页码与坐标）
    """
    root = ET.parse(str(xml_path)).getroot()
    blocks: list[Block] = []

    def add(
        section: str,
        block_type: str,
        text: str,
        table_ref: str | None = None,
        extra: dict | None = None,
        level: int | None = None,
    ) -> None:
        if text:
            blocks.append(
                Block(
                    f'x-{len(blocks)}', len(blocks), None, section, block_type, text, [], None, table_ref, extra, level
                )
            )

    title = root.find('.//article-meta/title-group/article-title')
    if title is not None:
        add('title', 'heading', _itertext(title), level=0)
    for abstract in root.findall('.//article-meta/abstract'):
        for p in abstract.iter('p'):
            add('abstract', 'paragraph', _itertext(p))

    floats = ('table-wrap', 'fig')

    def text_without_floats(el: ET.Element) -> str:
        # 段落里内嵌的表格/图单独成块，不混入段落文字
        parts = [el.text or '']
        for child in el:
            if child.tag not in floats:
                parts.append(''.join(child.itertext()))
            parts.append(child.tail or '')
        return re.sub(r'\s+', ' ', ''.join(parts)).strip()

    def add_float(el: ET.Element) -> None:
        label = el.findtext('label') or ''
        caption = _itertext(el.find('caption')) if el.find('caption') is not None else ''
        if el.tag == 'table-wrap':
            rows = [' | '.join(_itertext(c) for c in tr if c.tag in ('td', 'th')) for tr in el.iter('tr')]
            foot = _itertext(el.find('table-wrap-foot')) if el.find('table-wrap-foot') is not None else ''
            text = '\n'.join(x for x in (f'{label} {caption}'.strip(), *rows, foot) if x)
            add('table', 'table', text, label or None, {'caption': caption, 'footnote': foot})
        else:
            add('figure', 'caption', f'{label} {caption}'.strip(), extra={'kind': 'figure'})

    def walk(sec: ET.Element, section: str, depth: int = 0) -> None:
        for child in sec:
            tag = child.tag
            if tag == 'title':
                section = classify_heading(_itertext(child), section)
                add(section, 'heading', _itertext(child), level=max(depth, 1))
            elif tag == 'p':
                add(section, 'paragraph', text_without_floats(child))
                for fl in child.iter():
                    if fl.tag in floats:
                        add_float(fl)
            elif tag == 'sec':
                walk(child, section, depth + 1)
            elif tag in floats:
                add_float(child)
            elif tag in ('list', 'disp-quote', 'boxed-text'):
                add(section, 'list_item', _itertext(child))

    body = root.find('.//body')
    if body is not None:
        walk(body, 'introduction')
    for group in root.findall('.//floats-group'):  # PMC 常把表格/图集中放在正文之外
        for el in group:
            if el.tag in floats:
                add_float(el)
    for ref in root.findall('.//back/ref-list/ref'):
        add('references', 'list_item', _itertext(ref))
    return blocks


# ---------------- 入库前处理（入库与旧数据回填共用）----------------
TABLE_SPLIT_CHARS = 2000  # 超过此长度的表格按行组拆分
TABLE_PART_CHARS = 1200  # 每个行组的目标长度（不含表题、表头、脚注）
MIN_TABLE_LINES = 3  # 至少表头 + 2 行才拆
KEEP_REFERENCE_DESIGNS = ('指南/共识', '系统评价/Meta分析')  # 这些文献保留参考文献（推荐→引用、纳入研究匹配）


def keep_block(block: Block, keep_references: bool) -> bool:
    """
    致谢、基金、作者信息、页脚注一律舍弃；参考文献只在指南、共识、系统综述中保留
    """
    if block.section == 'backmatter':
        return False
    return keep_references or block.section != 'references'


def split_table(block: Block) -> list[Block]:
    """
    超长表格按行组拆成多个块：每块都带表题、表头行和脚注（原文照录，不加字）。
    块 ID 为原 ID 加 .1/.2…，原表其余块的 ID 不变
    """
    if block.block_type != 'table' or len(block.text) <= TABLE_SPLIT_CHARS:
        return [block]
    extra = block.extra or {}
    lines = block.text.split('\n')
    caption = lines.pop(0) if extra.get('caption') and lines and extra['caption'] in lines[0] else None
    footnote = lines.pop() if extra.get('footnote') and lines and lines[-1] == extra['footnote'] else None
    if len(lines) < MIN_TABLE_LINES:  # 只有表头和一两行，无法按行拆
        return [block]
    header, rows = lines[0], lines[1:]
    groups: list[list[str]] = [[]]
    size = 0
    for row in rows:
        if groups[-1] and size + len(row) > TABLE_PART_CHARS:
            groups.append([])
            size = 0
        groups[-1].append(row)
        size += len(row)
    if len(groups) == 1:
        return [block]
    parts = []
    for i, group in enumerate(groups, 1):
        text = '\n'.join(x for x in (caption, header, *group, footnote) if x)
        part_extra = {k: v for k, v in extra.items() if k != 'html'} | {'table_part': i, 'table_parts': len(groups)}
        if i == 1 and 'html' in extra:
            part_extra['html'] = extra['html']
        parts.append(
            Block(
                f'{block.block_key}.{i}',
                block.seq,
                block.page,
                block.section,
                'table',
                text,
                block.regions,
                block.page_end,
                block.table_ref,
                part_extra,
            )
        )
    return parts


def prepare_blocks(blocks: list[Block], keep_references: bool) -> list[Block]:
    """
    舍弃不入库的块、拆分超长表格、按阅读顺序重新编号 seq（块 ID 不变）
    """
    out = [part for b in blocks if keep_block(b, keep_references) for part in split_table(b)]
    for i, b in enumerate(out):
        b.seq = i
    return out


_jieba = None


def search_text(text: str) -> str:
    """
    关键词检索用文本：jieba 分词后以空格连接、转小写（中文按词切开，英文按原词）
    """
    global _jieba  # noqa: PLW0603  首次调用时才加载词典
    if _jieba is None:
        import logging  # noqa: PLC0415

        import jieba  # noqa: PLC0415

        jieba.setLogLevel(logging.WARNING)
        _jieba = jieba
    return ' '.join(w for w in _jieba.cut(text.lower()) if w.strip())


def body_language(blocks: list[Block]) -> str:
    """
    按正文（不含题名区、参考文献）判断语言：zh / ja / en / other
    """
    text = ''.join(b.text for b in blocks if b.section not in ('title', 'references', 'backmatter'))
    if not text:
        return 'other'
    kana = len(re.findall(r'[぀-ヿ]', text))
    han = len(re.findall(r'[一-鿿]', text))
    latin = len(re.findall(r'[A-Za-z]', text))
    if kana > 0.02 * len(text):
        return 'ja'
    if han > 0.2 * len(text):
        return 'zh'
    if latin > 0.3 * len(text):
        return 'en'
    return 'other'
