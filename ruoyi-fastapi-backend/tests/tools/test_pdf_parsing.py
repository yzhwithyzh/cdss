import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pymupdf
import pytest

from tools.pdf_parsing import (
    MinerUClient,
    MinerUError,
    PdfParseSettings,
    TokenPoolExhaustedError,
    assess_text_layer,
    ocr_pdf,
    read_pages,
)

ENGLISH = 'Cold snare polypectomy achieved complete resection in 91.8% of polyps (95% CI 85.1-96.0). ' * 4


def _settings(tmp_path: Path) -> PdfParseSettings:
    return replace(
        PdfParseSettings.load('dev'),
        output_dir=tmp_path / 'out',
        mineru_tokens=('test-token',),
        mineru_poll_interval=0,
        mineru_upload_workers=2,
        vlm_api_key='',
    )


def _text_pdf(path: Path, page_text: str, pages: int = 3) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 60, 560, 780), page_text, fontsize=9)
        page.insert_text((40, 40), 'Journal of Testing  Vol.1')  # 页眉
        page.insert_text((290, 820), f'·{i + 1}·')  # 页码
    doc.save(str(path))
    return path


def _image_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 300, 300), fill=(0.5, 0.5, 0.5))
    doc.save(str(path))
    return path


def test_text_layer_ok_and_running_heads_removed(tmp_path: Path) -> None:
    pdf = _text_pdf(tmp_path / 'a.pdf', ENGLISH)
    report = assess_text_layer(pdf)
    assert report.verdict == 'ok'
    assert not report.needs_ocr
    pages = read_pages(pdf)
    assert len(pages) == 3  # noqa: PLR2004
    assert all('Journal of Testing' not in p and '·1·' not in p for p in pages)
    assert 'Cold snare' in pages[0]


def test_scanned_pdf_needs_ocr(tmp_path: Path) -> None:
    report = assess_text_layer(_image_pdf(tmp_path / 'scan.pdf'))
    assert report.verdict == 'scanned'
    assert report.needs_ocr


def test_font_remapped_ascii_soup_is_garbled(tmp_path: Path) -> None:
    soup = "L\"$#4%7 L456'8$=>.4<)$%/#!\"#(84H+-/#!\"#%#6$8'+%85.+-+'(+#+%#45-46'-.+5+-8'4%#\".'%79 " * 6
    report = assess_text_layer(_text_pdf(tmp_path / 'g.pdf', soup))
    assert report.verdict == 'garbled'
    assert report.ascii_punct_ratio > 0.4  # noqa: PLR2004


def test_unreadable_file(tmp_path: Path) -> None:
    bad = tmp_path / 'bad.pdf'
    bad.write_bytes(b'not a pdf')
    report = assess_text_layer(bad)
    assert report.verdict == 'unreadable'
    assert report.error


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _mock_mineru(zip_bytes: bytes, calls: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(f'{request.method} {url}')
        if url.endswith('/file-urls/batch'):
            body = json.loads(request.content)
            assert request.headers['Authorization'] == 'Bearer test-token'
            assert body['enable_table'] is True
            return httpx.Response(200, json={'code': 0, 'data': {'batch_id': 'B1', 'file_urls': ['https://oss/u0']}})
        if url == 'https://oss/u0':
            return httpx.Response(200)
        if url.endswith('/extract-results/batch/B1'):
            state = 'running' if sum('extract-results' in c for c in calls) == 1 else 'done'
            item = {'file_name': '2800.pdf', 'state': state, 'full_zip_url': 'https://cdn/r.zip'}
            return httpx.Response(200, json={'code': 0, 'data': {'extract_result': [item]}})
        if url == 'https://cdn/r.zip':
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_mineru_submit_poll_download_and_skip_existing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    pdf = _text_pdf(tmp_path / '2800.pdf', ENGLISH, pages=1)
    calls: list[str] = []
    zip_bytes = _zip({'full.md': '# Title\n\ntext', '2800_content_list.json': '[]', 'layout.json': '{}'})
    client = MinerUClient(settings, transport=_mock_mineru(zip_bytes, calls))
    states: list[dict[str, str]] = []

    results = client.parse([pdf], on_change=states.append)

    assert [r.state for r in results] == ['done']
    assert results[0].markdown_path.read_text(encoding='utf-8').startswith('# Title')
    assert results[0].content_list_path.name == '2800_content_list.json'
    assert results[0].output_dir == settings.output_dir / 'mineru' / 'pipeline' / '2800'
    assert states == [{'2800.pdf': 'running'}, {'2800.pdf': 'done'}]

    calls.clear()
    again = client.parse([pdf])
    assert again[0].state == 'done'
    assert calls == []  # 已解析过：不再请求接口


def test_mineru_rejects_zip_slip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    pdf = _text_pdf(tmp_path / '2800.pdf', ENGLISH, pages=1)
    client = MinerUClient(settings, transport=_mock_mineru(_zip({'../../evil.md': 'x'}), []))
    with pytest.raises(MinerUError, match='非法路径'):
        client.parse([pdf])
    assert not (tmp_path / 'evil.md').exists()


def test_mineru_requires_token(tmp_path: Path) -> None:
    with pytest.raises(MinerUError, match='LIT_MINERU_TOKENS'):
        MinerUClient(replace(_settings(tmp_path), mineru_tokens=()))


def test_tokens_parsed_from_env_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('LIT_MINERU_TOKENS', 'sk-a, sk-b;sk-c,sk-a')
    monkeypatch.setenv('LIT_MINERU_TOKEN', 'sk-d')
    assert PdfParseSettings.load('dev').mineru_tokens == ('sk-a', 'sk-b', 'sk-c', 'sk-d')


class _MultiTokenMinerU:
    """
    模拟多账号 MinerU：记录每次申请上传用的 Token；quota_dead 中的 Token 返回额度用尽
    """

    def __init__(self, quota_dead: set[str] | None = None) -> None:
        self.quota_dead = quota_dead or set()
        self.submits: list[tuple[str, list[str]]] = []
        self.batches: dict[str, list[str]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        token = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if url.endswith('/file-urls/batch'):
            if token in self.quota_dead:
                return httpx.Response(200, json={'code': -60005, 'msg': 'daily quota exceeded'})
            names = [f['name'] for f in json.loads(request.content)['files']]
            bid = f'B{len(self.submits)}'
            self.submits.append((token, names))
            self.batches[bid] = names
            return httpx.Response(
                200, json={'code': 0, 'data': {'batch_id': bid, 'file_urls': [f'https://oss/{bid}/{n}' for n in names]}}
            )
        if url.startswith('https://oss/'):
            return httpx.Response(200)
        if '/extract-results/batch/' in url:
            bid = url.rsplit('/', 1)[1]
            items = [
                {'file_name': n, 'state': 'done', 'full_zip_url': f'https://cdn/{n}.zip'} for n in self.batches[bid]
            ]
            return httpx.Response(200, json={'code': 0, 'data': {'extract_result': items}})
        if url.startswith('https://cdn/'):
            return httpx.Response(200, content=_zip({'full.md': '# ok'}))
        return httpx.Response(404)


def test_tokens_used_in_order_and_switch_when_nearly_exhausted(tmp_path: Path) -> None:
    # 每个 Token 每天 10 页、预留 1 页 → 可用 9 页；每个 PDF 4 页
    settings = replace(
        _settings(tmp_path), mineru_tokens=('sk-a', 'sk-b', 'sk-c'), mineru_daily_pages=10, mineru_reserve_pages=1
    )
    pdfs = [_text_pdf(tmp_path / f'{i}.pdf', ENGLISH, pages=4) for i in range(1, 6)]
    api = _MultiTokenMinerU()
    client = MinerUClient(settings, transport=httpx.MockTransport(api.handler))

    results = client.parse(pdfs, max_files=10)

    assert [r.state for r in results] == ['done'] * 5
    assert api.submits == [('sk-a', ['1.pdf', '2.pdf']), ('sk-b', ['3.pdf', '4.pdf']), ('sk-c', ['5.pdf'])]
    status = client.pool.status()
    assert [s['used_pages'] for s in status] == [8, 8, 4]
    usage = (settings.output_dir / 'mineru' / 'token_usage.json').read_text(encoding='utf-8')
    assert 'sk-a' not in usage  # 只存指纹


def test_token_with_quota_error_is_skipped(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), mineru_tokens=('sk-a', 'sk-b'))
    pdf = _text_pdf(tmp_path / '1.pdf', ENGLISH, pages=1)
    api = _MultiTokenMinerU(quota_dead={'sk-a'})
    client = MinerUClient(settings, transport=httpx.MockTransport(api.handler))
    infos: list[str] = []

    results = client.parse([pdf], on_info=infos.append)

    assert results[0].state == 'done'
    assert api.submits == [('sk-b', ['1.pdf'])]
    assert client.pool.status()[0]['exhausted'] is True
    assert any('不可用' in m for m in infos)


def test_all_tokens_exhausted_stop_policy(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        mineru_tokens=('sk-a',),
        mineru_daily_pages=5,
        mineru_reserve_pages=0,
        mineru_when_exhausted='stop',
    )
    client = MinerUClient(settings, transport=httpx.MockTransport(_MultiTokenMinerU().handler))
    client.pool.record('sk-a', 5)
    with pytest.raises(TokenPoolExhaustedError):
        client.parse([_text_pdf(tmp_path / '1.pdf', ENGLISH, pages=1)])


def test_all_tokens_dead_does_not_loop_forever(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), mineru_tokens=('sk-a', 'sk-b'))
    api = _MultiTokenMinerU(quota_dead={'sk-a', 'sk-b'})
    client = MinerUClient(settings, transport=httpx.MockTransport(api.handler))
    with pytest.raises(MinerUError, match='均不可用'):
        client.parse([_text_pdf(tmp_path / '1.pdf', ENGLISH, pages=1)])


def test_ocr_without_engines_records_failed_pages_and_caches(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    pdf = _image_pdf(tmp_path / 'scan.pdf')
    first = ocr_pdf(pdf, settings)
    assert len(first.pages) == 1
    assert first.vlm_pages == 0
    assert first.cache_path.is_file()
    second = ocr_pdf(pdf, settings)
    assert second.cached
    assert second.pages == first.pages
