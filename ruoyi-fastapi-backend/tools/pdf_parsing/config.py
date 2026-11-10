"""
PDF 解析工具配置：与 Web 服务共用 .env.<env> 文件；密钥放 .env.<env>.local（不入库）
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _strip(value: str | None) -> str:
    return (value or '').strip().strip('\'"')


def _split_tokens(*values: str | None) -> tuple[str, ...]:
    """
    多个 Token 用逗号、分号或换行分隔；去重并保持顺序
    """
    tokens: list[str] = []
    for value in values:
        for part in re.split(r'[,;\s]+', _strip(value)):
            if part and part not in tokens:
                tokens.append(part)
    return tuple(tokens)


@dataclass(frozen=True)
class PdfParseSettings:
    """
    PDF 解析配置
    """

    output_dir: Path
    mineru_base_url: str
    mineru_tokens: tuple[str, ...]
    mineru_model: str
    mineru_language: str
    mineru_upload_workers: int
    mineru_poll_interval: float
    mineru_max_wait: float
    mineru_daily_pages: int
    mineru_reserve_pages: int
    mineru_when_exhausted: str
    vlm_base_url: str
    vlm_api_key: str
    vlm_model: str
    ocr_dpi: int
    ocr_workers: int

    @classmethod
    def load(cls, env_name: str = 'dev') -> 'PdfParseSettings':
        """
        读取配置，优先级：进程环境变量 > .env.<env>.local（本机私密配置，不入库）> .env.<env>

        :param env_name: 运行环境，如 dev、prod
        :return: 配置对象
        """
        env: dict[str, str | None] = {}
        for name in (f'.env.{env_name}', f'.env.{env_name}.local'):
            env_file = BACKEND_ROOT / name
            if env_file.is_file():
                env.update(dotenv_values(env_file))
        env.update(os.environ)
        output_dir = _strip(env.get('LIT_PARSE_OUTPUT_DIR'))
        data_root = _strip(env.get('LIT_DATA_ROOT'))
        if not output_dir:
            output_dir = str(Path(data_root) / '_parsed') if data_root else str(BACKEND_ROOT / 'uploads' / 'parsed')
        return cls(
            output_dir=Path(output_dir),
            mineru_base_url=_strip(env.get('LIT_MINERU_BASE_URL')) or 'https://mineru.net/api/v4',
            mineru_tokens=_split_tokens(env.get('LIT_MINERU_TOKENS'), env.get('LIT_MINERU_TOKEN')),
            mineru_model=_strip(env.get('LIT_MINERU_MODEL')) or 'pipeline',
            mineru_language=_strip(env.get('LIT_MINERU_LANGUAGE')) or 'ch',
            mineru_upload_workers=max(1, int(_strip(env.get('LIT_MINERU_UPLOAD_WORKERS')) or 12)),
            mineru_poll_interval=float(_strip(env.get('LIT_MINERU_POLL_SECONDS')) or 15),
            mineru_max_wait=float(_strip(env.get('LIT_MINERU_MAX_WAIT_SECONDS')) or 3600),
            mineru_daily_pages=int(_strip(env.get('LIT_MINERU_DAILY_PAGES')) or 1000),
            mineru_reserve_pages=int(_strip(env.get('LIT_MINERU_RESERVE_PAGES')) or 20),
            mineru_when_exhausted=_strip(env.get('LIT_MINERU_WHEN_EXHAUSTED')) or 'queue',
            vlm_base_url=_strip(env.get('LIT_VLM_BASE_URL')) or 'https://api.deepseek.com',
            vlm_api_key=_strip(env.get('LIT_VLM_API_KEY')),
            vlm_model=_strip(env.get('LIT_VLM_MODEL')) or 'deepseek-v4-flash-vision-exp',
            ocr_dpi=int(_strip(env.get('LIT_OCR_DPI')) or 200),
            ocr_workers=max(1, int(_strip(env.get('LIT_OCR_WORKERS')) or 4)),
        )
