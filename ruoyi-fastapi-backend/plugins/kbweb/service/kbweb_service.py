"""
循证知识库客户端服务层：调用 DAO，把 snake_case 键统一转成 camelCase
"""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from module_literature.service.literature_service import LiteratureService
from plugins.kbweb.dao.kbweb_dao import KbwebDao


def _camel_key(key: str) -> str:
    return re.sub(r'_([a-z0-9])', lambda m: m.group(1).upper(), key)


def camel(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {_camel_key(k) if isinstance(k, str) else k: camel(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [camel(x) for x in obj]
    return obj


def _data_root() -> Path | None:
    root = os.getenv('LIT_DATA_ROOT', '').strip().strip('\'"')
    return Path(root) if root else None


def _parse_output_dir() -> Path | None:
    out = os.getenv('LIT_PARSE_OUTPUT_DIR', '').strip().strip('\'"')
    if out:
        return Path(out)
    root = _data_root()
    return root / '_parsed' if root else None


class KbwebService:
    @staticmethod
    async def stats(db: AsyncSession) -> dict:
        return camel(await KbwebDao.stats(db))

    @staticmethod
    async def questions(db: AsyncSession) -> list:
        return camel(await KbwebDao.question_list(db))

    @staticmethod
    async def suggest(db: AsyncSession, q: str) -> list:
        return camel(await KbwebDao.suggest(db, q))

    @staticmethod
    async def search(db: AsyncSession, scope: str, f: dict) -> dict:
        fn = {
            'record': KbwebDao.search_records,
            'recommendation': KbwebDao.search_recommendations,
            'finding': KbwebDao.search_findings,
            'passage': KbwebDao.search_passages,
        }[scope]
        return camel(await fn(db, f))

    @staticmethod
    async def record(db: AsyncSession, record_id: int) -> dict | None:
        return camel(await KbwebDao.record_bundle(db, record_id))

    @staticmethod
    async def record_fulltext(db: AsyncSession, record_id: int) -> dict | None:
        return camel(await KbwebDao.record_fulltext(db, record_id))

    @staticmethod
    async def record_pdf(db: AsyncSession, record_id: int) -> Path | None:
        src = await KbwebDao.record_source(db, record_id)
        root = _data_root()
        if not src or not src['pdf_path'] or not root:
            return None
        return await asyncio.to_thread(LiteratureService._resolve_pdf, root, src['pdf_path'])

    @staticmethod
    async def record_image(db: AsyncSession, record_id: int, img_path: str) -> Path | None:
        """MinerU 解析出的图片：限定在该文献自己的解析目录内"""
        src = await KbwebDao.record_source(db, record_id)
        out = _parse_output_dir()
        if not src or not out:
            return None
        model = os.getenv('LIT_MINERU_MODEL', '').strip().strip('\'"') or 'pipeline'
        base = (out / 'mineru' / model / src['source_id']).resolve()
        target = (base / img_path).resolve()
        ok = target.is_relative_to(base) and target.suffix.lower() in {'.jpg', '.jpeg', '.png'}
        return target if ok and await asyncio.to_thread(target.is_file) else None

    @staticmethod
    async def concepts(db: AsyncSession, f: dict) -> dict:
        return camel(await KbwebDao.concept_list(db, f))

    @staticmethod
    async def concept(db: AsyncSession, concept_id: int) -> dict | None:
        return camel(await KbwebDao.concept_bundle(db, concept_id))

    @staticmethod
    async def question(db: AsyncSession, code: str) -> dict | None:
        return camel(await KbwebDao.question_bundle(db, code))

    @staticmethod
    async def compare(db: AsyncSession, q: str | None, concept_id: int | None) -> dict:
        return camel(await KbwebDao.compare(db, q, concept_id))
