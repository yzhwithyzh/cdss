from typing import Annotated, Literal

from fastapi import File, Form, Path, Query, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import Log
from common.aspect.db_session import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.enums import BusinessType
from common.router import APIRouterPro
from common.vo import DataResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from module_literature.entity.vo.job_vo import JobCreatedModel, JobOverviewModel
from module_literature.service.job_service import JobService
from utils.log_util import logger
from utils.response_util import ResponseUtil

job_controller = APIRouterPro(
    prefix='/literature/job', order_num=31, tags=['文献库-导入与判定任务'], dependencies=[PreAuthDependency()]
)


@job_controller.post(
    '',
    summary='上传 XML 并创建判定任务接口',
    description='保存 EndNote/Zotero 导出的 XML 并创建任务，由独立 worker 解析入库并判定研究类型',
    response_model=DataResponseModel[JobCreatedModel],
    dependencies=[UserInterfaceAuthDependency('literature:job:add')],
)
@Log(title='文献导入判定', business_type=BusinessType.IMPORT)
async def create_literature_job(
    request: Request,
    file: Annotated[UploadFile, File(description='XML 文件')],
    topic_id: Annotated[int, Form(alias='topicId', description='病种ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
    reclassify_existing: Annotated[
        bool, Form(alias='reclassifyExisting', description='与已有文献重复时是否也重新判定')
    ] = False,
) -> Response:
    created = await JobService.create_job_services(
        query_db, file, topic_id, reclassify_existing, current_user.user.user_name
    )
    logger.info(f'已创建文献判定任务#{created.job_id}')

    return ResponseUtil.success(msg='已上传，等待 worker 处理', data=created.model_dump(by_alias=True))


@job_controller.get(
    '/list',
    summary='获取判定任务列表接口',
    description='用于获取最近的导入与判定任务、进度及在线 worker',
    response_model=DataResponseModel[JobOverviewModel],
    dependencies=[UserInterfaceAuthDependency('literature:record:list')],
)
async def get_literature_job_list(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    topic_id: Annotated[int | None, Query(alias='topicId', description='病种ID')] = None,
) -> Response:
    overview = await JobService.get_job_overview_services(query_db, topic_id)
    return ResponseUtil.success(data=overview.model_dump(by_alias=True))


@job_controller.put(
    '/{job_id}/{action}',
    summary='控制判定任务接口',
    description='暂停（pause）、继续（resume）、取消（cancel）任务，或将失败文献重新排队（retry）',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('literature:job:edit')],
)
@Log(title='文献导入判定', business_type=BusinessType.UPDATE)
async def change_literature_job(
    request: Request,
    job_id: Annotated[int, Path(description='任务ID')],
    action: Annotated[Literal['pause', 'resume', 'cancel', 'retry'], Path(description='操作')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await JobService.change_job_services(query_db, job_id, action, current_user.user.user_name)
    logger.info(f'任务#{job_id} {action}：{result.message}')

    return ResponseUtil.success(msg=result.message)
