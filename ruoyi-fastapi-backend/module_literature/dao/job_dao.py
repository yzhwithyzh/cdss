from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from module_literature.entity.do.literature_do import LitJob, LitJobItem, LitWorker
from utils.time_util import TimezoneUtil

# 视为“正在进行或已完成”的任务状态，同一文件不允许重复导入
ACTIVE_STATUSES = ('pending', 'parsing', 'running', 'paused', 'completed')


class JobDao:
    """
    文献判定任务数据库操作层
    """

    @classmethod
    async def get_job_list(cls, db: AsyncSession, topic_id: int | None, limit: int) -> list[LitJob]:
        """
        获取最近的任务

        :param db: orm对象
        :param topic_id: 病种ID
        :param limit: 条数
        :return: 任务列表
        """
        query = select(LitJob).order_by(LitJob.job_id.desc()).limit(limit)
        if topic_id:
            query = query.where(LitJob.topic_id == topic_id)
        return list((await db.execute(query)).scalars().all())

    @classmethod
    async def get_item_counts(cls, db: AsyncSession, job_ids: list[int]) -> dict[int, dict[str, int]]:
        """
        统计任务明细各状态数量

        :param db: orm对象
        :param job_ids: 任务ID列表
        :return: {任务ID: {状态: 数量}}
        """
        if not job_ids:
            return {}
        rows = await db.execute(
            select(LitJobItem.job_id, LitJobItem.status, func.count())
            .where(LitJobItem.job_id.in_(job_ids))
            .group_by(LitJobItem.job_id, LitJobItem.status)
        )
        counts: dict[int, dict[str, int]] = {}
        for job_id, status, count in rows.all():
            counts.setdefault(job_id, {})[status] = count
        return counts

    @classmethod
    async def get_online_workers(cls, db: AsyncSession, within_seconds: int) -> list[LitWorker]:
        """
        获取心跳未超时的 worker

        :param db: orm对象
        :param within_seconds: 心跳有效期（秒）
        :return: worker 列表
        """
        threshold = TimezoneUtil.utc_now().timestamp() - within_seconds
        rows = (await db.execute(select(LitWorker).order_by(LitWorker.worker_id))).scalars().all()
        return [row for row in rows if row.heartbeat_time and row.heartbeat_time.timestamp() >= threshold]

    @classmethod
    async def get_job_for_update(cls, db: AsyncSession, job_id: int) -> LitJob | None:
        return (await db.execute(select(LitJob).where(LitJob.job_id == job_id).with_for_update())).scalars().first()

    @classmethod
    async def find_same_file_job(cls, db: AsyncSession, topic_id: int, file_sha256: str) -> LitJob | None:
        """
        查找同一病种下导入过同一文件的任务（不含已取消、失败的）

        :param db: orm对象
        :param topic_id: 病种ID
        :param file_sha256: 文件SHA-256
        :return: 任务
        """
        return (
            (
                await db.execute(
                    select(LitJob).where(
                        LitJob.topic_id == topic_id,
                        LitJob.file_sha256 == file_sha256,
                        LitJob.status.in_(ACTIVE_STATUSES),
                    )
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def reset_failed_items(cls, db: AsyncSession, job_id: int) -> int:
        """
        失败明细重新排队

        :param db: orm对象
        :param job_id: 任务ID
        :return: 重置条数
        """
        result = await db.execute(
            update(LitJobItem)
            .where(LitJobItem.job_id == job_id, LitJobItem.status == 'failed')
            .values(status='pending', attempts=0, locked_by=None, locked_time=None, finished_time=None)
        )
        return result.rowcount
