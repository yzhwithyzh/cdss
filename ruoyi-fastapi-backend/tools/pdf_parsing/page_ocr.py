"""
扫描件/乱码 PDF 的逐页 OCR：主用视觉大模型（OpenAI 兼容接口）按阅读顺序转录，失败时用本地 RapidOCR 兜底

结果缓存到 <output_dir>/ocr/<文件名>.json（{"pages": [...], "vlm_pages": n, "rapid_pages": n, ...}）。
RapidOCR 为可选依赖（pip install rapidocr_onnxruntime），未安装时只用视觉模型。
"""

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openai
import pymupdf

from tools.pdf_parsing.config import PdfParseSettings

PROMPT = (
    '逐字转录这页图片中的全部文字，按正常阅读顺序输出（双栏先左栏后右栏），保留标题编号、段落换行和表格行；'
    '不确定的字按最像的写，不要猜测补充、不要解释、不要翻译、不要省略。页面无文字则输出空。'
)
MIN_VLM_CHARS = 30  # 视觉模型输出少于此字数时尝试本地 OCR
VLM_RETRIES = 3

_rapid_engine: Any = None


@dataclass
class OcrResult:
    """
    单个 PDF 的 OCR 结果
    """

    file_name: str
    pages: list[str] = field(default_factory=list)
    vlm_pages: int = 0
    rapid_pages: int = 0
    failed_pages: int = 0
    cache_path: Path | None = None
    cached: bool = False


def vlm_page(client: openai.OpenAI, model: str, png: bytes) -> str | None:
    """
    视觉模型转录单页；连续失败返回 None
    """
    b64 = base64.b64encode(png).decode()
    for attempt in range(VLM_RETRIES):
        try:
            r = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=6000,
                messages=[
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': PROMPT},
                            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
                        ],
                    },
                ],
            )
            return (r.choices[0].message.content or '').strip()
        except openai.OpenAIError:  # noqa: PERF203  重试需要逐次捕获
            time.sleep(3 * (attempt + 1))
    return None


def rapid_page(png: bytes, page_width: float) -> str:
    """
    本地 RapidOCR 识别单页，并按单栏/双栏还原阅读顺序

    :param png: 页面 PNG
    :param page_width: 页面像素宽度
    :return: 页面文本
    """
    global _rapid_engine  # noqa: PLW0603
    if _rapid_engine is None:
        from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

        _rapid_engine = RapidOCR()
    res, _ = _rapid_engine(png)
    items = []
    for box, txt, _conf in res or []:
        xs = [q[0] for q in box]
        ys = [q[1] for q in box]
        items.append({'x0': min(xs), 'x1': max(xs), 'y': (min(ys) + max(ys)) / 2, 'txt': txt})
    left = [i for i in items if i['x1'] < page_width * 0.52]
    right = [i for i in items if i['x0'] > page_width * 0.48]
    if items and len(left) >= len(items) * 0.3 and len(right) >= len(items) * 0.3:
        spans = sorted([i for i in items if i not in left and i not in right], key=lambda i: i['y'])
        order = spans + sorted(left, key=lambda i: i['y']) + sorted(right, key=lambda i: i['y'])
    else:
        order = sorted(items, key=lambda i: (round(i['y'] / 12), i['x0']))
    return '\n'.join(i['txt'] for i in order)


def ocr_pdf(pdf_path: str | Path, settings: PdfParseSettings, use_cache: bool = True) -> OcrResult:
    """
    逐页 OCR 一个 PDF，结果写缓存；已有缓存时直接读取

    :param pdf_path: PDF 路径
    :param settings: 配置（视觉模型地址、Key、DPI、输出目录）
    :param use_cache: 是否复用缓存
    :return: OCR 结果
    """
    pdf_path = Path(pdf_path)
    cache = settings.output_dir / 'ocr' / f'{pdf_path.name}.json'
    if use_cache and cache.is_file():
        data = json.loads(cache.read_text(encoding='utf-8'))
        return OcrResult(
            pdf_path.name,
            data['pages'],
            data.get('vlm_pages', 0),
            data.get('rapid_pages', 0),
            data.get('failed_pages', 0),
            cache,
            cached=True,
        )
    client = (
        openai.OpenAI(api_key=settings.vlm_api_key, base_url=settings.vlm_base_url, timeout=180, max_retries=0)
        if settings.vlm_api_key
        else None
    )
    result = OcrResult(pdf_path.name, cache_path=cache)
    with pymupdf.open(str(pdf_path)) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=settings.ocr_dpi)
            png = pix.tobytes('png')
            text = vlm_page(client, settings.vlm_model, png) if client else None
            how = 'vlm' if text is not None else None
            if text is None or len(text) < MIN_VLM_CHARS:
                try:
                    alt = rapid_page(png, pix.width)
                    if len(alt) > len(text or ''):
                        text, how = alt, 'rapid'
                except ImportError:
                    pass
            if how == 'vlm':
                result.vlm_pages += 1
            elif how == 'rapid':
                result.rapid_pages += 1
            else:
                result.failed_pages += 1
            result.pages.append(text or '')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                'file': pdf_path.name,
                'dpi': settings.ocr_dpi,
                'model': settings.vlm_model if client else None,
                'pages': result.pages,
                'vlm_pages': result.vlm_pages,
                'rapid_pages': result.rapid_pages,
                'failed_pages': result.failed_pages,
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    return result
