import asyncio
import os
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import CrudResponseModel, PageModel
from exceptions.exception import ServiceException
from module_literature.dao.literature_dao import LiteratureDao
from module_literature.entity.do.literature_do import LitStudyDesign
from module_literature.entity.vo.literature_vo import (
    DeleteRecordModel,
    DesignLabelCountModel,
    EditRecordModel,
    PicoModel,
    RecordDetailModel,
    RecordPageQueryModel,
    ReviewDesignModel,
    StudyDesignModel,
    TopicModel,
)
from utils.time_util import TimezoneUtil

MANUAL_SOURCE = '人工审核'


class LiteratureService:
    """
    文献题录模块服务层
    """

    @classmethod
    async def get_topic_list_services(cls, query_db: AsyncSession) -> list[TopicModel]:
        """
        获取病种列表service

        :param query_db: orm对象
        :return: 病种列表
        """
        return [TopicModel.model_validate(topic) for topic in await LiteratureDao.get_topic_list(query_db)]

    @classmethod
    async def get_record_list_services(
        cls, query_db: AsyncSession, query_object: RecordPageQueryModel, is_page: bool = True
    ) -> PageModel | list[dict[str, Any]]:
        """
        获取文献题录列表service

        :param query_db: orm对象
        :param query_object: 查询参数对象
        :param is_page: 是否开启分页
        :return: 题录列表
        """
        return await LiteratureDao.get_record_list(query_db, query_object, is_page)

    @classmethod
    async def get_design_label_counts_services(
        cls, query_db: AsyncSession, topic_id: int | None
    ) -> list[DesignLabelCountModel]:
        """
        获取研究类型统计service

        :param query_db: orm对象
        :param topic_id: 病种ID
        :return: 研究类型及文献数
        """
        rows = await LiteratureDao.get_design_label_counts(query_db, topic_id)
        return [DesignLabelCountModel(design_label=label, count=count) for label, count in rows]

    @classmethod
    async def get_record_detail_services(cls, query_db: AsyncSession, record_id: int) -> RecordDetailModel:
        """
        获取文献题录详情service

        :param query_db: orm对象
        :param record_id: 题录ID
        :return: 题录详情，含研究类型判定记录与 PICO
        """
        found = await LiteratureDao.get_record_by_id(query_db, record_id)
        if not found:
            raise ServiceException(message='文献不存在')
        record, topic_name = found
        designs = await LiteratureDao.get_designs_by_record(query_db, record_id)
        picos = await LiteratureDao.get_picos_by_record(query_db, record_id)
        return RecordDetailModel(
            record_id=record.record_id,
            topic_id=record.topic_id,
            topic_name=topic_name,
            source_id=record.source_id,
            source_row=record.source_row,
            title=record.title,
            authors=record.authors,
            pub_year=record.pub_year,
            abstract=record.abstract,
            language=record.language,
            doi=record.doi,
            pmid=record.pmid,
            source_db=record.source_db,
            has_pdf=record.pdf_path is not None,
            remark=record.remark,
            designs=[StudyDesignModel.model_validate(design) for design in designs],
            picos=[PicoModel.model_validate(pico) for pico in picos],
        )

    @classmethod
    async def edit_record_services(cls, query_db: AsyncSession, edit_record: EditRecordModel) -> CrudResponseModel:
        """
        编辑文献题录信息service

        :param query_db: orm对象
        :param edit_record: 编辑参数
        :return: 操作结果
        """
        try:
            record = await LiteratureDao.get_record_for_update(query_db, edit_record.record_id)
            if not record:
                raise ServiceException(message='文献不存在')
            for field in ('title', 'authors', 'pub_year', 'abstract', 'language', 'doi', 'pmid', 'remark'):
                setattr(record, field, getattr(edit_record, field))
            record.update_by = edit_record.update_by
            await query_db.commit()
        except Exception:
            await query_db.rollback()
            raise
        return CrudResponseModel(is_success=True, message='修改成功')

    @classmethod
    async def delete_record_services(
        cls, query_db: AsyncSession, delete_record: DeleteRecordModel, operator: str
    ) -> CrudResponseModel:
        """
        删除文献题录service（逻辑删除）

        :param query_db: orm对象
        :param delete_record: 删除参数
        :param operator: 操作人账号
        :return: 操作结果
        """
        try:
            record_ids = [int(item) for item in delete_record.record_ids.split(',') if item.strip()]
        except ValueError as e:
            raise ServiceException(message='题录ID格式不正确') from e
        if not record_ids:
            raise ServiceException(message='传入题录ID为空')
        try:
            deleted = await LiteratureDao.soft_delete_records(query_db, record_ids, operator)
            if deleted == 0:
                raise ServiceException(message='文献不存在或已删除')
            await query_db.commit()
        except Exception:
            await query_db.rollback()
            raise
        return CrudResponseModel(is_success=True, message=f'已删除 {deleted} 篇文献')

    @classmethod
    async def review_design_services(
        cls, query_db: AsyncSession, review: ReviewDesignModel, operator: str
    ) -> CrudResponseModel:
        """
        审核研究类型：确认当前结果，或以人工结果替换当前结果（原结果保留为历史）

        :param query_db: orm对象
        :param review: 审核参数
        :param operator: 操作人账号
        :return: 操作结果
        """
        if not await LiteratureDao.get_record_by_id(query_db, review.record_id):
            raise ServiceException(message='文献不存在')
        now = TimezoneUtil.utc_now()
        try:
            current = await LiteratureDao.get_current_design_for_update(query_db, review.record_id)
            if review.action == 'confirm':
                if not current:
                    raise ServiceException(message='该文献尚无研究类型，无法确认')
                current.review_status = '1'
                current.reviewed_by = operator
                current.reviewed_time = now
                current.update_by = operator
                message = '已确认研究类型'
            else:
                label = (review.design_label or '').strip()
                if not label:
                    raise ServiceException(message='请填写新的研究类型')
                if current and current.design_label == label:
                    raise ServiceException(message='与当前研究类型相同，如认可请直接确认')
                if current:
                    current.is_current = False
                    current.update_by = operator
                    # 先写入旧结果的状态，避免与“每篇仅一条当前结果”的唯一索引冲突
                    await query_db.flush()
                query_db.add(
                    LitStudyDesign(
                        record_id=review.record_id,
                        design_label=label,
                        method='manual',
                        source=MANUAL_SOURCE,
                        rationale=(review.rationale or '').strip() or None,
                        is_current=True,
                        review_status='2',
                        reviewed_by=operator,
                        reviewed_time=now,
                        create_by=operator,
                        update_by=operator,
                    )
                )
                message = '已修改研究类型'
            await query_db.commit()
        except Exception:
            await query_db.rollback()
            raise
        return CrudResponseModel(is_success=True, message=message)

    @classmethod
    async def get_record_pdf_path_services(cls, query_db: AsyncSession, record_id: int) -> Path:
        """
        获取文献全文 PDF 的本地路径

        :param query_db: orm对象
        :param record_id: 题录ID
        :return: PDF 文件路径
        """
        found = await LiteratureDao.get_record_by_id(query_db, record_id)
        if not found or not found[0].pdf_path:
            raise ServiceException(message='该文献没有全文')
        data_root = os.getenv('LIT_DATA_ROOT', '').strip().strip('\'"')
        if not data_root:
            raise ServiceException(message='未配置文献数据目录 LIT_DATA_ROOT')
        pdf_file = await asyncio.to_thread(cls._resolve_pdf, Path(data_root), found[0].pdf_path)
        if not pdf_file:
            raise ServiceException(message='全文文件不存在')
        return pdf_file

    @staticmethod
    def _resolve_pdf(data_root: Path, relative_path: str) -> Path | None:
        """
        解析全文路径，限定在数据目录内且文件存在

        :param data_root: 文献数据目录
        :param relative_path: 相对路径
        :return: 文件路径，不存在或越界时为空
        """
        root = data_root.resolve()
        pdf_file = (root / relative_path).resolve()
        return pdf_file if pdf_file.is_relative_to(root) and pdf_file.is_file() else None
