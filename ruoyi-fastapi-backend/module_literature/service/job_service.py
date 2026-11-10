import hashlib
import os
import uuid
from pathlib import Path
from typing import Literal

import aiofiles
import aiofiles.os
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import CrudResponseModel
from exceptions.exception import ServiceException
from module_literature.dao.job_dao import JobDao
from module_literature.entity.do.literature_do import LitJob, LitTopic
from module_literature.entity.vo.job_vo import JobCreatedModel, JobModel, JobOverviewModel, WorkerModel
from module_literature.worker.config import upload_dir_from_env
from utils.time_util import TimezoneUtil

CHUNK_SIZE = 1024 * 1024
# 在文件开头这么多字节内应能找到 <record>，否则不是 EndNote/Zotero 导出的题录 XML
RECORD_PROBE_BYTES = 8 * 1024 * 1024
WORKER_ONLINE_SECONDS = 60
JOB_LIST_LIMIT = 30

JobAction = Literal['pause', 'resume', 'cancel', 'retry']


class JobService:
    """
    文献导入与研究类型判定任务服务层
    """

    @classmethod
    async def create_job_services(
        cls,
        query_db: AsyncSession,
        file: UploadFile,
        topic_id: int,
        reclassify_existing: bool,
        operator: str,
    ) -> JobCreatedModel:
        """
        保存上传的 XML 并创建任务，由独立 worker 解析与判定

        :param query_db: orm对象
        :param file: 上传文件
        :param topic_id: 病种ID
        :param reclassify_existing: 与已有文献重复时是否也重新判定
        :param operator: 操作人账号
        :return: 任务ID
        """
        filename = Path(file.filename or '').name
        if not filename.lower().endswith('.xml'):
            raise ServiceException(message='请上传 EndNote 或 Zotero 导出的 XML 文件')
        if not await query_db.get(LitTopic, topic_id):
            raise ServiceException(message='病种不存在')

        upload_dir = upload_dir_from_env(os.environ) / TimezoneUtil.utc_now().strftime('%Y%m')
        await aiofiles.os.makedirs(upload_dir, exist_ok=True)
        target = upload_dir / f'{uuid.uuid4().hex}.xml'
        digest = hashlib.sha256()
        size = 0
        head = b''
        try:
            async with aiofiles.open(target, 'wb') as out:
                while chunk := await file.read(CHUNK_SIZE):
                    if size < RECORD_PROBE_BYTES:
                        head += chunk
                    digest.update(chunk)
                    size += len(chunk)
                    await out.write(chunk)
            if size == 0:
                raise ServiceException(message='上传的文件为空')
            if not head.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<') or b'<record' not in head:
                raise ServiceException(
                    message='未在文件中找到题录记录（<record>），请确认是 EndNote 或 Zotero 导出的 XML'
                )
            file_sha256 = digest.hexdigest()
            same = await JobDao.find_same_file_job(query_db, topic_id, file_sha256)
            if same:
                raise ServiceException(message=f'该文件已在任务#{same.job_id}中导入过此病种')
            job = LitJob(
                topic_id=topic_id,
                status='pending',
                source_file=filename,
                file_path=str(target.resolve()),
                file_sha256=file_sha256,
                file_size=size,
                reclassify_existing=reclassify_existing,
                create_by=operator,
                update_by=operator,
            )
            query_db.add(job)
            await query_db.commit()
        except Exception:
            await query_db.rollback()
            if await aiofiles.os.path.exists(target):
                await aiofiles.os.remove(target)
            raise
        return JobCreatedModel(job_id=job.job_id)

    @classmethod
    async def get_job_overview_services(cls, query_db: AsyncSession, topic_id: int | None) -> JobOverviewModel:
        """
        获取最近的任务（含明细进度）与在线 worker

        :param query_db: orm对象
        :param topic_id: 病种ID
        :return: 任务与 worker 概况
        """
        jobs = await JobDao.get_job_list(query_db, topic_id, JOB_LIST_LIMIT)
        counts = await JobDao.get_item_counts(query_db, [job.job_id for job in jobs])
        workers = await JobDao.get_online_workers(query_db, WORKER_ONLINE_SECONDS)
        return JobOverviewModel(
            jobs=[JobModel.model_validate(job).model_copy(update=counts.get(job.job_id, {})) for job in jobs],
            workers=[WorkerModel.model_validate(worker) for worker in workers],
        )

    @classmethod
    async def change_job_services(
        cls, query_db: AsyncSession, job_id: int, action: JobAction, operator: str
    ) -> CrudResponseModel:
        """
        暂停、继续、取消任务，或将失败的文献重新排队

        :param query_db: orm对象
        :param job_id: 任务ID
        :param action: 操作
        :param operator: 操作人账号
        :return: 操作结果
        """
        try:
            job = await JobDao.get_job_for_update(query_db, job_id)
            if not job:
                raise ServiceException(message='任务不存在')
            message = await cls._apply_action(query_db, job, action)
            job.update_by = operator
            await query_db.commit()
        except Exception:
            await query_db.rollback()
            raise
        return CrudResponseModel(is_success=True, message=message)

    @classmethod
    async def _apply_action(cls, query_db: AsyncSession, job: LitJob, action: JobAction) -> str:
        # 已完成解析的任务恢复后直接进入判定，否则重新解析
        resume_status = 'running' if job.batch_id else 'pending'
        if action == 'pause':
            if job.status == 'parsing':
                raise ServiceException(message='正在解析文件，请等待解析完成后再暂停')
            if job.status not in ('pending', 'running'):
                raise ServiceException(message='只有待处理或判定中的任务可以暂停')
            job.status = 'paused'
            job.worker_id = None
            return '已暂停，正在判定的文献完成后停止'
        if action == 'resume':
            if job.status not in ('paused', 'cancelled'):
                raise ServiceException(message='只有已暂停或已取消的任务可以继续')
            job.status = resume_status
            job.worker_id = None
            job.error_message = None
            job.finished_time = None
            return '已继续，等待 worker 处理'
        if action == 'cancel':
            if job.status not in ('pending', 'parsing', 'running', 'paused'):
                raise ServiceException(message='该任务已结束')
            job.status = 'cancelled'
            job.worker_id = None
            job.finished_time = TimezoneUtil.utc_now()
            return '已取消，已完成判定的结果保留'
        if job.status in ('pending', 'parsing'):
            raise ServiceException(message='任务尚未开始判定')
        reset = await JobDao.reset_failed_items(query_db, job.job_id)
        if not reset and job.status != 'failed':
            raise ServiceException(message='没有需要重试的文献')
        job.status = resume_status
        job.worker_id = None
        job.error_message = None
        job.finished_time = None
        return f'已将 {reset} 篇失败文献重新排队' if reset else '已重新排队，等待 worker 重新解析'
