from typing import Annotated

from fastapi import Path, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic_validation_decorator import ValidateFields
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import Log
from common.aspect.db_session import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.enums import BusinessType
from common.router import APIRouterPro
from common.vo import DataResponseModel, PageResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from module_literature.entity.vo.literature_vo import (
    DeleteRecordModel,
    DesignLabelCountModel,
    EditRecordModel,
    RecordDetailModel,
    RecordPageQueryModel,
    RecordRowModel,
    ReviewDesignModel,
    TopicModel,
)
from module_literature.service.literature_service import LiteratureService
from utils.log_util import logger
from utils.response_util import ResponseUtil

literature_controller = APIRouterPro(
    prefix='/literature/record', order_num=30, tags=['文献库-文献题录'], dependencies=[PreAuthDependency()]
)


@literature_controller.get(
    '/topics',
    summary='获取病种列表接口',
    description='用于获取启用的病种专题列表',
    response_model=DataResponseModel[list[TopicModel]],
    dependencies=[UserInterfaceAuthDependency('literature:record:list')],
)
async def get_literature_topics(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    topics = await LiteratureService.get_topic_list_services(query_db)
    return ResponseUtil.success(data=[topic.model_dump(by_alias=True) for topic in topics])


@literature_controller.get(
    '/list',
    summary='获取文献题录分页列表接口',
    description='用于按病种、关键词、研究类型、年份等条件查询文献题录',
    response_model=PageResponseModel[RecordRowModel],
    dependencies=[UserInterfaceAuthDependency('literature:record:list')],
)
async def get_literature_record_list(
    request: Request,
    record_page_query: Annotated[RecordPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    record_page_query_result = await LiteratureService.get_record_list_services(
        query_db, record_page_query, is_page=True
    )
    logger.info('获取成功')

    return ResponseUtil.success(model_content=record_page_query_result)


@literature_controller.get(
    '/designLabels',
    summary='获取研究类型统计接口',
    description='用于获取当前研究类型及对应文献数',
    response_model=DataResponseModel[list[DesignLabelCountModel]],
    dependencies=[UserInterfaceAuthDependency('literature:record:list')],
)
async def get_literature_design_labels(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    topic_id: Annotated[int | None, Query(alias='topicId', description='病种ID')] = None,
) -> Response:
    labels = await LiteratureService.get_design_label_counts_services(query_db, topic_id)
    return ResponseUtil.success(data=[label.model_dump(by_alias=True) for label in labels])


@literature_controller.put(
    '',
    summary='编辑文献题录接口',
    description='用于修改文献题录信息（标题、作者、年份、摘要等）',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('literature:record:edit')],
)
@ValidateFields(validate_model='edit_record')
@Log(title='文献题录', business_type=BusinessType.UPDATE)
async def edit_literature_record(
    request: Request,
    edit_record: EditRecordModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    edit_record.update_by = current_user.user.user_name
    edit_result = await LiteratureService.edit_record_services(query_db, edit_record)
    logger.info(edit_result.message)

    return ResponseUtil.success(msg=edit_result.message)


@literature_controller.delete(
    '/{record_ids}',
    summary='删除文献题录接口',
    description='用于逻辑删除文献题录，数据与导入来源保留，列表和统计中不再出现',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('literature:record:remove')],
)
@Log(title='文献题录', business_type=BusinessType.DELETE)
async def delete_literature_record(
    request: Request,
    record_ids: Annotated[str, Path(description='需要删除的题录ID，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    delete_result = await LiteratureService.delete_record_services(
        query_db, DeleteRecordModel(record_ids=record_ids), current_user.user.user_name
    )
    logger.info(delete_result.message)

    return ResponseUtil.success(msg=delete_result.message)


@literature_controller.put(
    '/design/review',
    summary='审核研究类型接口',
    description='用于确认当前研究类型，或以人工结果替换（原结果保留为历史）',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('literature:design:review')],
)
@ValidateFields(validate_model='review')
@Log(title='文献研究类型', business_type=BusinessType.UPDATE)
async def review_literature_design(
    request: Request,
    review: ReviewDesignModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    review_result = await LiteratureService.review_design_services(query_db, review, current_user.user.user_name)
    logger.info(review_result.message)

    return ResponseUtil.success(msg=review_result.message)


@literature_controller.get(
    '/{record_id}/pdf',
    summary='获取文献全文接口',
    description='用于获取文献全文 PDF',
    dependencies=[UserInterfaceAuthDependency('literature:record:query')],
)
async def get_literature_record_pdf(
    request: Request,
    record_id: Annotated[int, Path(description='题录ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    pdf_file = await LiteratureService.get_record_pdf_path_services(query_db, record_id)
    return FileResponse(pdf_file, media_type='application/pdf', content_disposition_type='inline')


@literature_controller.get(
    '/{record_id}',
    summary='获取文献题录详情接口',
    description='用于获取文献题录详情、研究类型判定记录及 PICO',
    response_model=DataResponseModel[RecordDetailModel],
    dependencies=[UserInterfaceAuthDependency('literature:record:query')],
)
async def get_literature_record_detail(
    request: Request,
    record_id: Annotated[int, Path(description='题录ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    record_detail = await LiteratureService.get_record_detail_services(query_db, record_id)
    logger.info(f'获取record_id为{record_id}的信息成功')

    return ResponseUtil.success(data=record_detail.model_dump(by_alias=True))
