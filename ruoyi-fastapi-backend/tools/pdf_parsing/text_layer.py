"""
本地 PDF 文本层读取与质量判定（PyMuPDF）
"""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# 私用区字符与 NUL：读取文本时剔除
_PUA = chr(0xE000) + '-' + chr(0xF8FF)  # 私用区
PUA_RE = re.compile('[' + _PUA + chr(0) + ']')
# 中文期刊自定义字体常见的乱码：控制字符、替换符、私用区
GARBLED_RE = re.compile(
    '[' + chr(1) + '-' + chr(8) + chr(11) + chr(12) + chr(14) + '-' + chr(31) + chr(0xFFFD) + _PUA + ']'
)
PAGE_NUMBER_RE = re.compile(r'[·•\s]*\d{1,5}[·•\s]*')

# 平均每页可读字符低于此值视为扫描件（沿用原项目阈值）
SCANNED_CHARS_PER_PAGE = 150
# 乱码字符占比高于此值视为文本层不可信，需走 OCR
GARBLED_RATIO = 0.02
# 中文期刊字体重映射会把数字和拉丁字母变成 ASCII 标点串（如 'L"$#4%7'）。
# 试点 413 篇实测：正常文献 ASCII 中标点占比 ≤0.25（英文 ≤0.17），乱码文献均 ≥0.54
ASCII_PUNCT_RATIO = 0.40
ASCII_MIN_CHARS = 200
_ASCII_PUNCT = frozenset(chr(c) for c in range(33, 127) if not chr(c).isalnum())


@dataclass(frozen=True)
class TextLayerReport:
    """
    文本层质量报告
    """

    pages: int
    chars: int
    chars_per_page: float
    garbled_ratio: float
    verdict: str  # ok / scanned / garbled / unreadable
    ascii_punct_ratio: float = 0.0
    error: str | None = None

    @property
    def needs_ocr(self) -> bool:
        return self.verdict in ('scanned', 'garbled')


def strip_running_heads(pages: list[str]) -> list[str]:
    """
    去掉页眉页脚：在 ≥40% 页面重复出现的短行（期刊名、卷期），以及纯页码行（如 ·2469·）

    :param pages: 每页文本
    :return: 处理后的每页文本
    """
    n = len(pages)
    repeated: set[str] = set()
    if n >= 3:  # noqa: PLR2004
        counter: Counter[str] = Counter()
        for text in pages:
            counter.update({line.strip() for line in text.splitlines() if 3 <= len(line.strip()) <= 80})  # noqa: PLR2004
        repeated = {line for line, c in counter.items() if c >= max(3, int(n * 0.4))}
    out = []
    for text in pages:
        keep = [
            line
            for line in text.splitlines()
            if line.strip() not in repeated and not PAGE_NUMBER_RE.fullmatch(line.strip())
        ]
        out.append('\n'.join(keep))
    return out


def read_pages(pdf_path: str | Path, strip_heads: bool = True) -> list[str]:
    """
    读取 PDF 文本层，按页返回（去私用区字符与 NUL；可选去页眉页脚）

    :param pdf_path: PDF 路径
    :param strip_heads: 是否去页眉页脚
    :return: 每页文本
    """
    with pymupdf.open(str(pdf_path)) as doc:
        pages = [PUA_RE.sub('', page.get_text('text') or '') for page in doc]
    return strip_running_heads(pages) if strip_heads else pages


def assess_text_layer(pdf_path: str | Path) -> TextLayerReport:
    """
    判定文本层是否可用：ok 直接用；scanned / garbled 需 OCR；unreadable 文件损坏或加密

    :param pdf_path: PDF 路径
    :return: 质量报告
    """
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            if doc.needs_pass:
                return TextLayerReport(doc.page_count, 0, 0, 0, 'unreadable', error='encrypted')
            raw = [page.get_text('text') or '' for page in doc]
    except Exception as e:
        return TextLayerReport(0, 0, 0, 0, 'unreadable', error=f'{type(e).__name__}: {e}')
    n = len(raw)
    visible = re.sub(r'\s', '', ''.join(raw))
    garbled = len(GARBLED_RE.findall(visible))
    readable = len(visible) - garbled
    per_page = readable / max(1, n)
    ratio = garbled / max(1, len(visible))
    ascii_alnum = sum(1 for ch in visible if ch.isascii() and ch.isalnum())
    ascii_punct = sum(1 for ch in visible if ch in _ASCII_PUNCT)
    ascii_total = ascii_alnum + ascii_punct
    punct_ratio = ascii_punct / ascii_total if ascii_total >= ASCII_MIN_CHARS else 0.0
    if n == 0 or per_page < SCANNED_CHARS_PER_PAGE:
        verdict = 'scanned'
    elif ratio > GARBLED_RATIO or punct_ratio > ASCII_PUNCT_RATIO:
        verdict = 'garbled'
    else:
        verdict = 'ok'
    return TextLayerReport(n, readable, round(per_page, 1), round(ratio, 4), verdict, round(punct_ratio, 3))
