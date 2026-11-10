"""
MinerU 在线 API（v4）客户端：批量申请上传地址 → 并行上传 → 轮询 → 下载解析 zip 并解压

输出目录 <output_dir>/mineru/<model>/<名称>/ 下包含 full.md、*_content_list.json、layout.json 等。
多 Token：按顺序使用，当天额度快用完时切换到下一个（见 token_pool.py）。
说明：直连 mineru.net（不走系统代理）；官方每天有高优先级页数额度，超出部分排队处理。
"""

import io
import re
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
import pymupdf

from tools.pdf_parsing.config import PdfParseSettings
from tools.pdf_parsing.token_pool import MinerUTokenPool, TokenChoice

UPLOAD_RETRIES = 3
# 接口返回这些状态码或信息时，视为当前 Token 额度用尽/失效，切换下一个
ROTATE_STATUS = {401, 403, 429}
ROTATE_PATTERN = re.compile(
    r'quota|limit|exceed|insufficient|unauthori|forbidden|invalid.?token|expired|额度|上限|超出|次数|余额|无效|过期|未授权',
    re.IGNORECASE,
)


class MinerUError(RuntimeError):
    """
    MinerU 接口错误
    """


class TokenRotateError(MinerUError):
    """
    当前 Token 额度用尽或认证失败，需要换下一个
    """


@dataclass
class MinerUResult:
    """
    单个文件的解析结果
    """

    file_name: str
    state: str  # done / failed
    output_dir: Path | None = None
    markdown_path: Path | None = None
    content_list_path: Path | None = None
    error: str | None = None


def output_name(file_name: str) -> str:
    """
    输出目录名：取文件名开头的数字序号（规避 Windows 路径长度限制），否则做安全化截断

    :param file_name: 文件名，如 '36-中成药….pdf'、'2800.pdf'
    :return: 目录名，如 '36'、'2800'
    """
    m = re.match(r'(\d+)', file_name)
    return m.group(1) if m else re.sub(r'[^A-Za-z0-9_.-]', '_', file_name)[:40]


def data_id(path: Path) -> str:
    """
    MinerU 的 data_id 只允许字母数字及 _ - .
    """
    return re.sub(r'[^A-Za-z0-9_.-]', '_', path.name)[:120]


def page_count(path: Path) -> int:
    """
    PDF 页数（用于额度计算）；读不出时按 1 页计
    """
    try:
        with pymupdf.open(str(path)) as doc:
            return max(1, doc.page_count)
    except Exception:
        return 1


class MinerUClient:
    """
    MinerU v4 批量解析客户端（多 Token 轮换）
    """

    def __init__(
        self,
        settings: PdfParseSettings,
        transport: httpx.BaseTransport | None = None,
        pool: MinerUTokenPool | None = None,
    ) -> None:
        if not settings.mineru_tokens and pool is None:
            raise MinerUError('未配置 LIT_MINERU_TOKENS')
        self.settings = settings
        self.base_url = settings.mineru_base_url.rstrip('/')
        self.pool = pool or MinerUTokenPool(
            list(settings.mineru_tokens),
            settings.output_dir / 'mineru' / 'token_usage.json',
            daily_pages=settings.mineru_daily_pages,
            reserve_pages=settings.mineru_reserve_pages,
            when_exhausted=settings.mineru_when_exhausted,
        )
        self._transport = transport

    def _client(self, timeout: float = 120) -> httpx.Client:
        return httpx.Client(trust_env=False, timeout=timeout, transport=self._transport)

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    @staticmethod
    def _check(resp: httpx.Response, action: str) -> dict:
        """
        解析接口响应：额度/认证类错误抛 TokenRotateError，其余错误抛 MinerUError
        """
        try:
            body = resp.json()
        except ValueError:
            body = {'code': resp.status_code, 'msg': resp.text[:200]}
        if resp.status_code in ROTATE_STATUS:
            raise TokenRotateError(f'{action}: HTTP {resp.status_code} {body}')
        if body.get('code') != 0:
            message = f'{body.get("msg") or ""} {body.get("code")}'
            if ROTATE_PATTERN.search(message):
                raise TokenRotateError(f'{action}: {body}')
            raise MinerUError(f'{action}失败: {body}')
        return body

    def submit(
        self,
        paths: list[Path],
        token: str,
        model: str | None = None,
        ocr: bool = False,
        no_cache: bool = False,
    ) -> str:
        """
        用指定 Token 申请上传地址并并行上传文件

        :param paths: PDF 路径列表
        :param token: MinerU Token
        :param model: pipeline 或 vlm，默认取配置
        :param ocr: 是否强制 OCR（扫描件）
        :param no_cache: 是否忽略 MinerU 端缓存
        :return: batch_id
        """
        body = {
            'files': [{'name': p.name, 'is_ocr': ocr, 'data_id': data_id(p)} for p in paths],
            'model_version': model or self.settings.mineru_model,
            'enable_table': True,
            'enable_formula': False,
            'language': self.settings.mineru_language,
            'no_cache': no_cache,
        }
        with self._client() as c:
            r = self._check(
                c.post(f'{self.base_url}/file-urls/batch', headers=self._headers(token), json=body), '申请上传地址'
            )
        batch_id, urls = r['data']['batch_id'], r['data']['file_urls']

        def upload(pair: tuple[Path, str]) -> str | None:
            path, url = pair
            err = ''
            for attempt in range(UPLOAD_RETRIES):
                try:
                    with self._client(timeout=600) as cc:
                        resp = cc.put(url, content=path.read_bytes())
                    if resp.status_code == 200:  # noqa: PLR2004
                        return None
                    err = f'{resp.status_code} {resp.text[:80]}'
                except httpx.HTTPError as e:
                    err = str(e)[:120]
                time.sleep(5 * (attempt + 1))
            return f'上传失败 {path.name}: {err}'

        with ThreadPoolExecutor(max_workers=self.settings.mineru_upload_workers) as ex:
            errors = [e for e in ex.map(upload, list(zip(paths, urls, strict=True))) if e]
        if errors:
            raise MinerUError('; '.join(errors[:3]))
        return batch_id

    def poll(
        self,
        batch_id: str,
        token: str,
        on_change: Callable[[dict[str, str]], None] | None = None,
    ) -> list[dict]:
        """
        轮询批次直到全部 done/failed（须用提交该批次的 Token）

        :param batch_id: 批次 ID
        :param token: 提交时使用的 Token
        :param on_change: 状态变化回调（文件名 → 状态）
        :return: extract_result 列表
        """
        t0, last = time.time(), None
        with self._client() as c:
            while time.time() - t0 < self.settings.mineru_max_wait:
                resp = c.get(f'{self.base_url}/extract-results/batch/{batch_id}', headers=self._headers(token))
                results = self._check(resp, '查询批次')['data']['extract_result']
                states = {x['file_name']: x['state'] for x in results}
                if states != last and on_change:
                    on_change(states)
                last = states
                if all(s in ('done', 'failed') for s in states.values()):
                    return results
                time.sleep(self.settings.mineru_poll_interval)
        raise TimeoutError(f'MinerU 批次 {batch_id} 超时')

    def download(self, item: dict, model: str | None = None) -> MinerUResult:
        """
        下载并解压单个文件的解析结果

        :param item: poll 返回的单项
        :param model: 用于区分输出目录
        :return: 解析结果
        """
        name = item['file_name']
        if item['state'] != 'done':
            return MinerUResult(name, 'failed', error=item.get('err_msg') or item['state'])
        out = self.settings.output_dir / 'mineru' / (model or self.settings.mineru_model) / output_name(name)
        with self._client(timeout=300) as c:
            content = c.get(item['full_zip_url']).content
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            root = out.resolve()
            for member in zf.namelist():  # 第三方压缩包：拒绝越出输出目录的路径
                if not (root / member).resolve().is_relative_to(root):
                    raise MinerUError(f'解析结果包含非法路径: {member}')
            zf.extractall(out)
        md = next(iter(sorted(out.glob('*.md'))), None)
        content_list = next(iter(sorted(out.glob('*content_list.json'))), None)
        return MinerUResult(name, 'done', out, md, content_list)

    def _existing(self, path: Path, model: str) -> MinerUResult | None:
        out = self.settings.output_dir / 'mineru' / model / output_name(path.name)
        md = next(iter(sorted(out.glob('*.md'))), None) if out.is_dir() else None
        if not md:
            return None
        return MinerUResult(path.name, 'done', out, md, next(iter(sorted(out.glob('*content_list.json'))), None))

    @staticmethod
    def _plan_batch(todo: list[Path], pages: dict[Path, int], choice: TokenChoice, max_files: int) -> list[Path]:
        """
        从待处理文件中取一批：不超过文件数上限，且（未超额时）总页数不超过该 Token 剩余额度
        """
        batch, total = [], 0
        for p in todo:
            if len(batch) >= max_files:
                break
            if batch and not choice.over_quota and total + pages[p] > choice.capacity:
                continue
            batch.append(p)
            total += pages[p]
        return batch

    def parse(
        self,
        paths: list[Path],
        model: str | None = None,
        ocr: bool = False,
        no_cache: bool = False,
        skip_existing: bool = True,
        max_files: int = 20,
        on_change: Callable[[dict[str, str]], None] | None = None,
        on_info: Callable[[str], None] | None = None,
    ) -> list[MinerUResult]:
        """
        按 Token 额度分批提交、轮询、下载；已有 full.md 的文件默认跳过

        :param max_files: 每批文件数上限
        :param on_info: 切换 Token、额度等提示信息回调
        :return: 每个文件的结果（跳过的也会返回，state=done）
        """
        model = model or self.settings.mineru_model
        info = on_info or (lambda _msg: None)
        results: list[MinerUResult] = []
        todo: list[Path] = []
        for p in paths:
            existing = self._existing(p, model) if skip_existing else None
            if existing:
                results.append(existing)
            else:
                todo.append(p)
        pages = {p: page_count(p) for p in todo}
        rotate_failures = 0
        while todo:
            choice = self.pool.choose(pages[todo[0]])
            batch = self._plan_batch(todo, pages, choice, max_files)
            batch_pages = sum(pages[p] for p in batch)
            info(
                f'Token {choice.token[:5]}…{choice.fp}: 本批 {len(batch)} 个文件 {batch_pages} 页，'
                f'{"已超当日额度（排队处理）" if choice.over_quota else f"可用额度 {choice.capacity} 页"}'
            )
            try:
                batch_id = self.submit(batch, choice.token, model, ocr, no_cache)
            except TokenRotateError as e:
                rotate_failures += 1
                self.pool.mark_exhausted(choice.token)
                info(f'Token {choice.token[:5]}…{choice.fp} 不可用，切换下一个：{str(e)[:120]}')
                if rotate_failures > len(self.pool.tokens):
                    raise MinerUError('所有 MinerU Token 均不可用') from e
                continue
            rotate_failures = 0
            self.pool.record(choice.token, batch_pages)
            results.extend(self.download(item, model) for item in self.poll(batch_id, choice.token, on_change))
            done = set(batch)
            todo = [p for p in todo if p not in done]
        return results
