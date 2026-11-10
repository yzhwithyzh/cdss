"""
EndNote / Zotero 导出 XML 的流式解析

字段路径参考 SmartEBM title_and_abstract_screening_tool 的 RobustTextExtractor；
改为 iterparse 逐条读取并及时释放，数万条记录时内存占用与单条记录相当。
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

CJK = re.compile(r'[一-鿿]')
YEAR = re.compile(r'\b(1[89]\d{2}|20\d{2})\b')
TAG = re.compile(r'<[^>]+>')
NON_WORD = re.compile(r'[\W_]+', re.UNICODE)
DOI = re.compile(r'10\.\d{4,9}/\S+', re.IGNORECASE)


@dataclass
class ParsedRecord:
    """
    从 XML 解析出的一条题录
    """

    seq: int
    rec_number: str | None
    title: str | None
    authors: str | None
    pub_year: int | None
    abstract: str | None
    language: str
    doi: str | None
    pmid: str | None


def _text(element: ET.Element | None) -> str | None:
    """
    取元素下全部文本（兼容 EndNote 的 <style> 包裹与 Zotero 的纯文本）

    :param element: XML 元素
    :return: 去除首尾空白后的文本，为空时返回 None
    """
    if element is None:
        return None
    text = ' '.join(part.strip() for part in element.itertext() if part and part.strip())
    return text or None


def _first(record: ET.Element, paths: list[str]) -> ET.Element | None:
    for path in paths:
        element = record.find(path)
        if element is not None:
            return element
    return None


def normalize_title(title: str | None) -> str:
    """
    去重用的标题归一化：去标签、标点与空白，转小写

    :param title: 标题
    :return: 归一化后的标题
    """
    if not title:
        return ''
    return NON_WORD.sub('', TAG.sub('', title)).lower()


def normalize_doi(doi: str | None) -> str | None:
    """
    提取并统一 DOI（小写，去掉 https://doi.org/ 等前缀）

    :param doi: 原始 DOI 文本
    :return: DOI，无法识别时返回 None
    """
    if not doi:
        return None
    match = DOI.search(doi)
    return match.group(0).rstrip('.,;').lower() if match else None


def _language(record: ET.Element, title: str | None) -> str:
    language = (_text(record.find('.//language')) or '').lower()
    if 'chi' in language or 'zh' in language or '中' in language:
        return 'zh'
    return 'zh' if title and CJK.search(title) else 'en'


def _parse_record(record: ET.Element, seq: int) -> ParsedRecord:
    title = _text(_first(record, ['.//titles/title', './/title']))
    authors = [_text(author) for author in record.findall('.//contributors/authors/author')]
    if not authors:
        authors = [_text(author) for author in record.findall('.//author')]
    year_text = _text(_first(record, ['.//dates/year', './/year', './/pub-dates/date/year']))
    year_match = YEAR.search(year_text or '')
    accession = _text(record.find('.//accession-num'))
    return ParsedRecord(
        seq=seq,
        rec_number=_text(record.find('rec-number')),
        title=title,
        authors='; '.join(author for author in authors if author) or None,
        pub_year=int(year_match.group(1)) if year_match else None,
        abstract=_text(_first(record, ['.//abstract', './/abstracts/abstract'])),
        language=_language(record, title),
        doi=normalize_doi(_text(record.find('.//electronic-resource-num'))),
        pmid=accession if accession and accession.isdigit() else None,
    )


def iter_records(xml_path: Path) -> Iterator[ParsedRecord]:
    """
    逐条解析 XML 中的 <record>

    :param xml_path: XML 文件路径
    :return: 题录迭代器
    """
    seq = 0
    parent_stack: list[ET.Element] = []
    for event, element in ET.iterparse(xml_path, events=('start', 'end')):
        if event == 'start':
            parent_stack.append(element)
            continue
        parent_stack.pop()
        if element.tag != 'record':
            continue
        seq += 1
        yield _parse_record(element, seq)
        element.clear()
        # 从父节点移除已处理的 record，避免空元素在内存中累积
        if parent_stack:
            parent_stack[-1].remove(element)
