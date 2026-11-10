"""
命令行入口（在 ruoyi-fastapi-backend 目录下运行）：

    python -m tools.pdf_parsing check  <pdf...>                       # 文本层质量判定
    python -m tools.pdf_parsing mineru <pdf...> [--model pipeline|vlm] [--ocr] [--batch 20]
    python -m tools.pdf_parsing ocr    <pdf...>                       # 扫描件/乱码逐页 OCR
    python -m tools.pdf_parsing auto   <pdf...>                       # 先判定：可用→MinerU，扫描/乱码→OCR
    python -m tools.pdf_parsing tokens                                # 查看各 MinerU Token 今日用量
公共参数：--env dev（读取 .env.dev 与 .env.dev.local）
"""

import argparse
import sys
from pathlib import Path

from tools.pdf_parsing.config import PdfParseSettings
from tools.pdf_parsing.mineru_client import MinerUClient
from tools.pdf_parsing.page_ocr import ocr_pdf
from tools.pdf_parsing.text_layer import assess_text_layer


def _paths(items: list[str]) -> list[Path]:
    paths = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            paths.extend(sorted(p.glob('*.pdf')))
        elif p.is_file():
            paths.append(p)
        else:
            print(f'跳过（不存在）: {item}', file=sys.stderr)
    return paths


def _mineru(paths: list[Path], settings: PdfParseSettings, model: str | None, ocr: bool, batch: int) -> None:
    client = MinerUClient(settings)
    results = client.parse(
        paths, model=model, ocr=ocr, max_files=batch, on_change=lambda s: print('  ', s), on_info=print
    )
    for r in results:
        print(f'  {r.state:6} {r.file_name}  {r.markdown_path or r.error}')
    _tokens(client)


def _tokens(client: MinerUClient) -> None:
    print(f'MinerU Token 今日用量（{client.pool.today()}，每个 {client.pool.daily_pages} 页）：')
    for s in client.pool.status():
        flag = '  已标记用尽' if s['exhausted'] else ''
        print(f'  {s["token"]}  已用 {s["used_pages"]:>5} 页  剩余 {s["remaining"]:>5} 页{flag}')


def _ocr(paths: list[Path], settings: PdfParseSettings) -> None:
    for p in paths:
        r = ocr_pdf(p, settings)
        print(
            f'OCR {p.name}: 页数={len(r.pages)} 视觉模型={r.vlm_pages} 本地={r.rapid_pages} 失败={r.failed_pages}'
            f'{" (缓存)" if r.cached else ""} → {r.cache_path}'
        )


def main() -> None:
    ap = argparse.ArgumentParser(prog='python -m tools.pdf_parsing', description='PDF 解析工具')
    ap.add_argument('command', choices=['check', 'mineru', 'ocr', 'auto', 'tokens'])
    ap.add_argument('pdfs', nargs='*', help='PDF 文件或目录')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--model', default=None, help='MinerU 模型：pipeline 或 vlm')
    ap.add_argument('--ocr', action='store_true', help='MinerU 强制 OCR')
    ap.add_argument('--batch', type=int, default=20, help='MinerU 每批文件数')
    a = ap.parse_args()
    settings = PdfParseSettings.load(a.env)
    if a.command == 'tokens':
        _tokens(MinerUClient(settings))
        return
    if not a.pdfs:
        ap.error('需要指定 PDF 文件或目录')
    paths = _paths(a.pdfs)

    if a.command == 'check':
        for p in paths:
            r = assess_text_layer(p)
            print(
                f'{r.verdict:10} pages={r.pages:<4} chars/page={r.chars_per_page:<8} garbled={r.garbled_ratio:<7} ascii_punct={r.ascii_punct_ratio:<6} {p.name}'
                f'{"  " + r.error if r.error else ""}'
            )
    elif a.command == 'mineru':
        _mineru(paths, settings, a.model, a.ocr, a.batch)
    elif a.command == 'ocr':
        _ocr(paths, settings)
    else:
        reports = {p: assess_text_layer(p) for p in paths}
        ok = [p for p, r in reports.items() if r.verdict == 'ok']
        need_ocr = [p for p, r in reports.items() if r.needs_ocr]
        bad = [p for p, r in reports.items() if r.verdict == 'unreadable']
        print(f'文本层可用 {len(ok)}，需 OCR {len(need_ocr)}，无法读取 {len(bad)}')
        if ok:
            _mineru(ok, settings, a.model, False, a.batch)
        if need_ocr and settings.vlm_api_key:
            _ocr(need_ocr, settings)
        elif need_ocr:  # 未配置视觉模型：交给 MinerU 强制 OCR（忽略不可信的文本层）
            print(f'未配置 LIT_VLM_API_KEY，{len(need_ocr)} 个扫描/乱码文件改用 MinerU OCR 模式')
            _mineru(need_ocr, settings, a.model, True, a.batch)


if __name__ == '__main__':
    main()
