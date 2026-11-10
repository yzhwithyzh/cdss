from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from common.vo import PageModel
from module_literature.entity.do.literature_do import LitPico, LitRecord, LitStudyDesign, LitTopic
from module_literature.entity.vo.literature_vo import RecordPageQueryModel
from utils.page_util import PageUtil
from utils.time_util import TimezoneUtil

# 列表筛选时“未判定研究类型”的取值
NO_DESIGN = '__none__'


def _escape_like(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class LiteratureDao:
    """
    文献题录模块数据库操作层
    """

    @classmethod
    async def get_topic_list(cls, db: AsyncSession) -> list[LitTopic]:
        """
        获取启用的病种列表

        :param db: orm对象
        :return: 病种列表
        """
        return list(
            (
                await db.execute(
                    select(LitTopic).where(LitTopic.status == '0').order_by(LitTopic.order_num, LitTopic.topic_id)
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def get_record_list(
        cls, db: AsyncSession, query_object: RecordPageQueryModel, is_page: bool = True
    ) -> PageModel | list[dict[str, Any]]:
        """
        根据查询参数获取文献题录列表，附带当前研究类型

        :param db: orm对象
        :param query_object: 查询参数对象
        :param is_page: 是否开启分页
        :return: 题录列表
        """
        current = aliased(LitStudyDesign)
        conditions = [LitRecord.del_flag == '0']
        if query_object.topic_id:
            conditions.append(LitRecord.topic_id == query_object.topic_id)
        if query_object.keyword and query_object.keyword.strip():
            pattern = f'%{_escape_like(query_object.keyword.strip())}%'
            conditions.append(
                or_(LitRecord.title.ilike(pattern, escape='\\'), LitRecord.abstract.ilike(pattern, escape='\\'))
            )
        if query_object.source_id and query_object.source_id.strip():
            conditions.append(LitRecord.source_id == query_object.source_id.strip())
        if query_object.design_label == NO_DESIGN:
            conditions.append(current.design_id.is_(None))
        elif query_object.design_label:
            conditions.append(current.design_label == query_object.design_label)
        if query_object.review_status:
            conditions.append(current.review_status == query_object.review_status)
        if query_object.language:
            conditions.append(LitRecord.language == query_object.language)
        if query_object.has_pdf:
            conditions.append(
                LitRecord.pdf_path.isnot(None) if query_object.has_pdf == '1' else LitRecord.pdf_path.is_(None)
            )
        if query_object.has_abstract:
            conditions.append(
                LitRecord.abstract.isnot(None) if query_object.has_abstract == '1' else LitRecord.abstract.is_(None)
            )
        if query_object.begin_year:
            conditions.append(LitRecord.pub_year >= query_object.begin_year)
        if query_object.end_year:
            conditions.append(LitRecord.pub_year <= query_object.end_year)

        query = (
            select(
                LitRecord.record_id,
                LitRecord.topic_id,
                LitRecord.source_id,
                LitRecord.title,
                LitRecord.authors,
                LitRecord.pub_year,
                LitRecord.language,
                LitRecord.pdf_path.isnot(None).label('has_pdf'),
                LitRecord.abstract.isnot(None).label('has_abstract'),
                current.design_label,
                current.method.label('design_method'),
                current.review_status,
            )
            .outerjoin(current, and_(current.record_id == LitRecord.record_id, current.is_current.is_(True)))
            .where(*conditions)
            .order_by(LitRecord.record_id)
        )
        return await PageUtil.paginate(db, query, query_object.page_num, query_object.page_size, is_page)

    @classmethod
    async def get_design_label_counts(cls, db: AsyncSession, topic_id: int | None) -> list[tuple[str, int]]:
        """
        统计当前研究类型的文献数

        :param db: orm对象
        :param topic_id: 病种ID，为空时统计全部病种
        :return: (研究类型, 文献数) 列表，按文献数降序
        """
        query = (
            select(LitStudyDesign.design_label, func.count())
            .join(LitRecord, LitRecord.record_id == LitStudyDesign.record_id)
            .where(LitStudyDesign.is_current.is_(True), LitRecord.del_flag == '0')
            .group_by(LitStudyDesign.design_label)
            .order_by(func.count().desc())
        )
        if topic_id:
            query = query.where(LitRecord.topic_id == topic_id)
        return [(label, count) for label, count in (await db.execute(query)).all()]

    @classmethod
    async def get_record_by_id(cls, db: AsyncSession, record_id: int) -> tuple[LitRecord, str | None] | None:
        """
        根据题录ID获取题录及病种名称

        :param db: orm对象
        :param record_id: 题录ID
        :return: (题录, 病种名称)
        """
        row = (
            await db.execute(
                select(LitRecord, LitTopic.topic_name)
                .outerjoin(LitTopic, LitTopic.topic_id == LitRecord.topic_id)
                .where(LitRecord.record_id == record_id, LitRecord.del_flag == '0')
            )
        ).first()
        return (row[0], row[1]) if row else None

    @classmethod
    async def get_record_for_update(cls, db: AsyncSession, record_id: int) -> LitRecord | None:
        """
        获取并锁定未删除的题录

        :param db: orm对象
        :param record_id: 题录ID
        :return: 题录
        """
        return (
            (
                await db.execute(
                    select(LitRecord)
                    .where(LitRecord.record_id == record_id, LitRecord.del_flag == '0')
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def soft_delete_records(cls, db: AsyncSession, record_ids: list[int], operator: str) -> int:
        """
        逻辑删除题录（保留数据与导入来源，列表和统计中不再出现）

        :param db: orm对象
        :param record_ids: 题录ID列表
        :param operator: 操作人账号
        :return: 删除条数
        """
        result = await db.execute(
            update(LitRecord)
            .where(LitRecord.record_id.in_(record_ids), LitRecord.del_flag == '0')
            .values(del_flag='2', update_by=operator, update_time=TimezoneUtil.utc_now())
        )
        return result.rowcount

    @classmethod
    async def get_designs_by_record(cls, db: AsyncSession, record_id: int) -> list[LitStudyDesign]:
        """
        获取题录的全部研究类型判定记录，当前结果在前，其余按时间倒序

        :param db: orm对象
        :param record_id: 题录ID
        :return: 判定记录列表
        """
        return list(
            (
                await db.execute(
                    select(LitStudyDesign)
                    .where(LitStudyDesign.record_id == record_id)
                    .order_by(LitStudyDesign.is_current.desc(), LitStudyDesign.design_id.desc())
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def get_current_design_for_update(cls, db: AsyncSession, record_id: int) -> LitStudyDesign | None:
        """
        获取并锁定题录当前研究类型

        :param db: orm对象
        :param record_id: 题录ID
        :return: 当前研究类型
        """
        return (
            (
                await db.execute(
                    select(LitStudyDesign)
                    .where(LitStudyDesign.record_id == record_id, LitStudyDesign.is_current.is_(True))
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_picos_by_record(cls, db: AsyncSession, record_id: int) -> list[LitPico]:
        """
        获取题录的 PICO 要素

        :param db: orm对象
        :param record_id: 题录ID
        :return: PICO 列表
        """
        return list(
            (await db.execute(select(LitPico).where(LitPico.record_id == record_id).order_by(LitPico.pico_id)))
            .scalars()
            .all()
        )
