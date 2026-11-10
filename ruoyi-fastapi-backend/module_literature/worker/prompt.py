"""
研究类型判定提示词与结果解析

“原文设计描述”的抽取规则沿用 SmartEBM 标题摘要筛选工具 TASK 1 的要求（提取最具体的设计表述、
不做推测）；在此基础上增加按本系统分类体系归类、置信度与理由。只判定研究类型，不抽取 PICO。
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

CATEGORY_FILE = Path(__file__).with_name('design_categories.json')
NOT_REPORTED = {'', 'not reported', 'not available', 'none', 'n/a', '未报告', '无', '不适用'}
JSON_BLOCK = re.compile(r'\{.*\}', re.DOTALL)
SPACES = re.compile(r'\s+')


@dataclass(frozen=True)
class CategorySet:
    """
    研究类型分类体系
    """

    version: str
    fallback: str
    labels: tuple[str, ...]
    descriptions: tuple[str, ...]

    @classmethod
    def load(cls, path: Path = CATEGORY_FILE) -> 'CategorySet':
        data = json.loads(path.read_text(encoding='utf-8'))
        labels = tuple(item['label'] for item in data['categories'])
        if data['fallback'] not in labels:
            raise ValueError('fallback 必须是分类之一')
        return cls(
            version=data['version'],
            fallback=data['fallback'],
            labels=labels,
            descriptions=tuple(item.get('desc', '') for item in data['categories']),
        )

    def match(self, label: str | None) -> str | None:
        """
        将模型输出的类别对齐到分类体系（忽略空白与括号写法差异）

        :param label: 模型输出
        :return: 分类体系中的标签，无法对齐时为 None
        """
        if not label:
            return None
        key = SPACES.sub('', label).replace('（', '(').replace('）', ')')
        for candidate in self.labels:
            if SPACES.sub('', candidate).replace('（', '(').replace('）', ')') == key:
                return candidate
        return None


class DesignResult(BaseModel):
    """
    模型返回的结构化结果
    """

    study_design_text: str = Field(default='Not reported')
    category: str
    confidence: float | None = None
    reason: str | None = None

    @field_validator('confidence', mode='before')
    @classmethod
    def clamp_confidence(cls, value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return min(1.0, max(0.0, number))

    @field_validator('reason', mode='before')
    @classmethod
    def empty_to_none(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return None if text.lower() in NOT_REPORTED else text


def build_system_prompt(categories: CategorySet) -> str:
    """
    生成系统提示词

    :param categories: 分类体系
    :return: 系统提示词
    """
    category_lines = '\n'.join(
        f'- {label}：{desc}' for label, desc in zip(categories.labels, categories.descriptions, strict=True)
    )
    return f"""You are a medical research methodologist. Read the title and abstract of one article and complete the tasks below.

TASK 1 — study_design_text: Extract the MOST SPECIFIC study design explicitly reported. Always extract the most specific design mentioned, not general categories. For example:
- If a study is described as a "network meta-analysis", extract that specific term, not just "meta-analysis" or "systematic review"
- If a study is described as a "prospective cohort study", extract the complete term, not just "cohort study"
- If a study is described as a "randomized double-blind placebo-controlled trial", extract the complete description
- Only extract what is explicitly stated, without making assumptions. If the design is not explicitly stated, answer "Not reported".

TASK 2 — category: Classify the article into exactly ONE category from the list below. Copy the label exactly as written.
Judge by the design actually used to answer the article's own research question, not by studies cited in the background.
If the design is not explicitly stated, infer it from the methods described; if only a title is available, infer cautiously and lower the confidence.
{category_lines}

TASK 3 — confidence: a number from 0 to 1 for the category decision. reason: one short sentence in Chinese explaining the category decision.

Respond with a single JSON object only, no markdown, using exactly these keys:
{{"study_design_text": "", "category": "", "confidence": 0.0, "reason": ""}}"""


def build_user_prompt(title: str, abstract: str | None) -> str:
    """
    生成用户提示词

    :param title: 标题
    :param abstract: 摘要
    :return: 用户提示词
    """
    return f'Title: {title}\n\nAbstract: {abstract or "No abstract available."}'


def parse_result(content: str, categories: CategorySet) -> tuple[DesignResult, str, bool]:
    """
    解析模型输出

    :param content: 模型返回文本
    :param categories: 分类体系
    :return: (结果, 对齐后的类别, 类别是否超出分类体系)
    """
    text = content.strip()
    if text.startswith('```'):
        text = text.strip('`').removeprefix('json').strip()
    match = JSON_BLOCK.search(text)
    if not match:
        raise ValueError(f'模型未返回JSON：{content[:200]}')
    result = DesignResult.model_validate(json.loads(match.group(0)))
    label = categories.match(result.category)
    return result, label or categories.fallback, label is None
