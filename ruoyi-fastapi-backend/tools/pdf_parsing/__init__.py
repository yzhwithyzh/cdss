"""
PDF 解析工具：本地文本层判定、MinerU 在线解析、扫描件逐页 OCR

    from tools.pdf_parsing import PdfParseSettings, assess_text_layer, MinerUClient, ocr_pdf

    settings = PdfParseSettings.load('dev')
    report = assess_text_layer('pdf/2800.pdf')          # ok / scanned / garbled / unreadable
    if report.needs_ocr:
        pages = ocr_pdf('pdf/2800.pdf', settings).pages
    else:
        results = MinerUClient(settings).parse([Path('pdf/2800.pdf')])

命令行：python -m tools.pdf_parsing --help
"""

from tools.pdf_parsing.config import PdfParseSettings
from tools.pdf_parsing.mineru_client import MinerUClient, MinerUError, MinerUResult
from tools.pdf_parsing.page_ocr import OcrResult, ocr_pdf
from tools.pdf_parsing.text_layer import TextLayerReport, assess_text_layer, read_pages, strip_running_heads
from tools.pdf_parsing.token_pool import MinerUTokenPool, TokenPoolExhaustedError

__all__ = [
    'MinerUClient',
    'MinerUError',
    'MinerUResult',
    'MinerUTokenPool',
    'OcrResult',
    'PdfParseSettings',
    'TextLayerReport',
    'TokenPoolExhaustedError',
    'assess_text_layer',
    'ocr_pdf',
    'read_pages',
    'strip_running_heads',
]
