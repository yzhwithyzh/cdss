"""
循证知识库客户端网站接口（只读、免登录）
"""

from typing import Annotated, Literal

from fastapi import Path, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from common.aspect.db_session import DBSessionDependency
from common.router import APIRouterPro
from plugins.kbweb.service.kbweb_service import KbwebService
from utils.response_util import ResponseUtil

kbweb_public_controller = APIRouterPro(prefix='/kbweb/public', order_num=40, tags=['循证知识库-客户端'])

DB = Annotated[AsyncSession, DBSessionDependency()]
PageNum = Annotated[int, Query(alias='pageNum', ge=1)]
PageSize = Annotated[int, Query(alias='pageSize', ge=1, le=100)]


@kbweb_public_controller.get('/stats', summary='首页统计')
async def stats(request: Request, query_db: DB) -> Response:
    return ResponseUtil.success(data=await KbwebService.stats(query_db))


@kbweb_public_controller.get('/questions', summary='循证问题列表')
async def questions(request: Request, query_db: DB) -> Response:
    return ResponseUtil.success(data=await KbwebService.questions(query_db))


@kbweb_public_controller.get('/suggest', summary='检索联想（概念/文献标题）')
async def suggest(request: Request, query_db: DB, q: str = Query(min_length=1, max_length=100)) -> Response:
    return ResponseUtil.success(data=await KbwebService.suggest(query_db, q.strip()))


@kbweb_public_controller.get('/search', summary='综合检索：题录 / 推荐意见 / 研究发现 / 原文段落')
async def search(  # noqa: PLR0913
    request: Request,
    query_db: DB,
    scope: Literal['record', 'recommendation', 'finding', 'passage'] = 'record',
    q: str | None = Query(None, max_length=200),
    year_from: int | None = Query(None, alias='yearFrom'),
    year_to: int | None = Query(None, alias='yearTo'),
    design: str | None = None,
    lang: str | None = None,
    eq: str | None = None,
    structured: bool = False,
    has_pdf: bool = Query(False, alias='hasPdf'),
    sort: str | None = None,
    strength: str | None = None,
    certainty: str | None = None,
    action_type: str | None = Query(None, alias='actionType'),
    guideline_id: int | None = Query(None, alias='guidelineId'),
    finding_type: str | None = Query(None, alias='findingType'),
    measure: str | None = None,
    statement_role: str | None = Query(None, alias='statementRole'),
    concept_id: int | None = Query(None, alias='conceptId'),
    record_id: int | None = Query(None, alias='recordId'),
    block_type: str | None = Query(None, alias='blockType'),
    page_num: PageNum = 1,
    page_size: PageSize = 20,
) -> Response:
    f = {k: v for k, v in locals().items() if k not in ('request', 'query_db', 'scope')}
    f['q'] = (q or '').strip() or None
    return ResponseUtil.success(data=await KbwebService.search(query_db, scope, f))


@kbweb_public_controller.get('/record/{record_id}', summary='文献详情（题录 + 结构化抽取）')
async def record(request: Request, query_db: DB, record_id: Annotated[int, Path()]) -> Response:
    data = await KbwebService.record(query_db, record_id)
    return ResponseUtil.success(data=data) if data else ResponseUtil.failure(msg='文献不存在')


@kbweb_public_controller.get('/record/{record_id}/fulltext', summary='结构化全文（解析块 + 溯源片段位置）')
async def record_fulltext(request: Request, query_db: DB, record_id: Annotated[int, Path()]) -> Response:
    data = await KbwebService.record_fulltext(query_db, record_id)
    return ResponseUtil.success(data=data) if data else ResponseUtil.failure(msg='该文献没有结构化全文')


@kbweb_public_controller.get('/record/{record_id}/pdf', summary='全文 PDF')
async def record_pdf(request: Request, query_db: DB, record_id: Annotated[int, Path()]):
    path = await KbwebService.record_pdf(query_db, record_id)
    if not path:
        return ResponseUtil.failure(msg='全文文件不存在')
    return FileResponse(path, media_type='application/pdf', content_disposition_type='inline')


@kbweb_public_controller.get('/record/{record_id}/image', summary='全文解析出的图片')
async def record_image(request: Request, query_db: DB, record_id: Annotated[int, Path()], path: str = Query(min_length=1, max_length=300)):
    target = await KbwebService.record_image(query_db, record_id, path)
    if not target:
        return ResponseUtil.failure(msg='图片不存在')
    return FileResponse(target, headers={'Cache-Control': 'public, max-age=86400'})


@kbweb_public_controller.get('/concepts', summary='概念列表')
async def concepts(  # noqa: PLR0913
    request: Request,
    query_db: DB,
    q: str | None = Query(None, max_length=100),
    concept_type: str | None = Query(None, alias='conceptType'),
    include_unused: bool = Query(False, alias='includeUnused'),
    page_num: PageNum = 1,
    page_size: PageSize = 50,
) -> Response:
    f = {'q': (q or '').strip() or None, 'concept_type': concept_type, 'include_unused': include_unused, 'page_num': page_num, 'page_size': page_size}
    return ResponseUtil.success(data=await KbwebService.concepts(query_db, f))


@kbweb_public_controller.get('/concept/{concept_id}', summary='概念详情：别名、关系、相关推荐与研究发现')
async def concept(request: Request, query_db: DB, concept_id: Annotated[int, Path()]) -> Response:
    data = await KbwebService.concept(query_db, concept_id)
    return ResponseUtil.success(data=data) if data else ResponseUtil.failure(msg='概念不存在')


@kbweb_public_controller.get('/question/{code}', summary='循证问题证据地图')
async def question(request: Request, query_db: DB, code: Annotated[str, Path(max_length=20)]) -> Response:
    data = await KbwebService.question(query_db, code)
    return ResponseUtil.success(data=data) if data else ResponseUtil.failure(msg='循证问题不存在')


@kbweb_public_controller.get('/compare', summary='跨指南推荐对比')
async def compare(
    request: Request, query_db: DB, q: str | None = Query(None, max_length=100), concept_id: int | None = Query(None, alias='conceptId')
) -> Response:
    return ResponseUtil.success(data=await KbwebService.compare(query_db, (q or '').strip() or None, concept_id))
