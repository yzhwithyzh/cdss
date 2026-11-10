from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel
from pydantic_validation_decorator import NotBlank, Size, Xss

from common.types import ApiUtcDateTime


class TopicModel(BaseModel):
    """
    病种专题模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    topic_id: int | None = Field(default=None, description='病种ID')
    topic_code: str | None = Field(default=None, description='病种编码')
    topic_name: str | None = Field(default=None, description='病种名称')


class RecordPageQueryModel(BaseModel):
    """
    文献题录分页查询模型
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    topic_id: int | None = Field(default=None, description='病种ID')
    keyword: str | None = Field(default=None, description='标题或摘要关键词')
    source_id: str | None = Field(default=None, description='来源编号')
    design_label: str | None = Field(default=None, description='当前研究类型；__none__ 表示未判定')
    review_status: Literal['0', '1', '2'] | None = Field(default=None, description='审核状态')
    language: Literal['zh', 'en'] | None = Field(default=None, description='语言')
    has_pdf: Literal['0', '1'] | None = Field(default=None, description='是否有全文（1有 0无）')
    has_abstract: Literal['0', '1'] | None = Field(default=None, description='是否有摘要（1有 0无）')
    begin_year: int | None = Field(default=None, description='起始年份')
    end_year: int | None = Field(default=None, description='截止年份')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class RecordRowModel(BaseModel):
    """
    文献题录列表行模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    record_id: int = Field(description='题录ID')
    topic_id: int = Field(description='病种ID')
    source_id: str = Field(description='来源编号')
    title: str = Field(description='标题')
    authors: str | None = Field(default=None, description='作者')
    pub_year: int | None = Field(default=None, description='发表年份')
    language: str | None = Field(default=None, description='语言')
    has_pdf: bool = Field(description='是否有全文')
    has_abstract: bool = Field(description='是否有摘要')
    design_label: str | None = Field(default=None, description='当前研究类型')
    design_method: str | None = Field(default=None, description='当前研究类型判定方式')
    review_status: str | None = Field(default=None, description='当前研究类型审核状态')


class StudyDesignModel(BaseModel):
    """
    研究类型判定模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    design_id: int | None = Field(default=None, description='研究类型判定ID')
    record_id: int | None = Field(default=None, description='题录ID')
    design_label: str | None = Field(default=None, description='研究类型')
    design_code: str | None = Field(default=None, description='标准研究类型编码')
    method: str | None = Field(default=None, description='判定方式')
    source: str | None = Field(default=None, description='判定来源')
    model: str | None = Field(default=None, description='模型名称')
    confidence: float | None = Field(default=None, description='置信度')
    rationale: str | None = Field(default=None, description='判定依据')
    is_current: bool | None = Field(default=None, description='是否当前采用结果')
    review_status: str | None = Field(default=None, description='审核状态')
    reviewed_by: str | None = Field(default=None, description='审核人')
    reviewed_time: ApiUtcDateTime | None = Field(default=None, description='审核时间')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: ApiUtcDateTime | None = Field(default=None, description='创建时间')


class PicoModel(BaseModel):
    """
    PICO 要素模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    participants: str | None = Field(default=None, description='研究对象')
    intervention: str | None = Field(default=None, description='干预/暴露')
    comparison: str | None = Field(default=None, description='对照')
    outcomes: str | None = Field(default=None, description='结局')
    method: str | None = Field(default=None, description='来源方式')
    source: str | None = Field(default=None, description='来源')


class RecordDetailModel(BaseModel):
    """
    文献题录详情模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    record_id: int = Field(description='题录ID')
    topic_id: int = Field(description='病种ID')
    topic_name: str | None = Field(default=None, description='病种名称')
    source_id: str = Field(description='来源编号')
    source_row: int | None = Field(default=None, description='来源文件行号')
    title: str = Field(description='标题')
    authors: str | None = Field(default=None, description='作者')
    pub_year: int | None = Field(default=None, description='发表年份')
    abstract: str | None = Field(default=None, description='摘要')
    language: str | None = Field(default=None, description='语言')
    doi: str | None = Field(default=None, description='DOI')
    pmid: str | None = Field(default=None, description='PubMed ID')
    source_db: str | None = Field(default=None, description='检索来源数据库')
    has_pdf: bool = Field(description='是否有全文')
    remark: str | None = Field(default=None, description='备注')
    designs: list[StudyDesignModel] = Field(default_factory=list, description='研究类型判定记录（当前结果在前）')
    picos: list[PicoModel] = Field(default_factory=list, description='PICO 要素')


class DesignLabelCountModel(BaseModel):
    """
    研究类型统计模型
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    design_label: str = Field(description='研究类型')
    count: int = Field(description='文献数')


class ReviewDesignModel(BaseModel):
    """
    研究类型审核模型：确认当前结果，或改为新的研究类型
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    record_id: int = Field(description='题录ID')
    action: Literal['confirm', 'modify'] = Field(description='confirm确认当前结果 modify改为新的研究类型')
    design_label: str | None = Field(default=None, description='新的研究类型（modify 时必填）')
    rationale: str | None = Field(default=None, description='修改理由')

    @Xss(field_name='design_label', message='研究类型不能包含脚本字符')
    @Size(field_name='design_label', min_length=0, max_length=100, message='研究类型不能超过100个字符')
    def get_design_label(self) -> str | None:
        return self.design_label

    @Xss(field_name='rationale', message='修改理由不能包含脚本字符')
    @Size(field_name='rationale', min_length=0, max_length=2000, message='修改理由不能超过2000个字符')
    def get_rationale(self) -> str | None:
        return self.rationale

    def validate_fields(self) -> None:
        self.get_design_label()
        self.get_rationale()


class EditRecordModel(BaseModel):
    """
    编辑文献题录模型（仅题录信息，研究类型通过审核接口修改）
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    record_id: int = Field(description='题录ID')
    title: str | None = Field(default=None, description='标题')
    authors: str | None = Field(default=None, description='作者')
    pub_year: int | None = Field(default=None, ge=1800, le=2100, description='发表年份')
    abstract: str | None = Field(default=None, description='摘要')
    language: Literal['zh', 'en'] | None = Field(default=None, description='语言')
    doi: str | None = Field(default=None, description='DOI')
    pmid: str | None = Field(default=None, description='PubMed ID')
    remark: str | None = Field(default=None, description='备注')
    update_by: str | None = Field(default=None, description='更新者')

    @field_validator('title', 'authors', 'abstract', 'doi', 'pmid', 'remark', mode='before')
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        """
        去除首尾空白，空字符串存为空

        :param value: 输入值
        :return: 清洗后的值
        """
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @Xss(field_name='title', message='标题不能包含脚本字符')
    @NotBlank(field_name='title', message='标题不能为空')
    def get_title(self) -> str | None:
        return self.title

    @Size(field_name='doi', min_length=0, max_length=255, message='DOI不能超过255个字符')
    def get_doi(self) -> str | None:
        return self.doi

    @Size(field_name='pmid', min_length=0, max_length=20, message='PMID不能超过20个字符')
    def get_pmid(self) -> str | None:
        return self.pmid

    @Size(field_name='remark', min_length=0, max_length=500, message='备注不能超过500个字符')
    def get_remark(self) -> str | None:
        return self.remark

    def validate_fields(self) -> None:
        self.get_title()
        self.get_doi()
        self.get_pmid()
        self.get_remark()


class DeleteRecordModel(BaseModel):
    """
    删除文献题录模型
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    record_ids: str = Field(description='需要删除的题录ID，逗号分隔')
