from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from common.types import ApiUtcDateTime


class JobModel(BaseModel):
    """
    文献导入与研究类型判定任务模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    job_id: int = Field(description='任务ID')
    topic_id: int = Field(description='病种ID')
    status: str = Field(description='状态')
    source_file: str = Field(description='上传文件名')
    file_size: int | None = Field(default=None, description='文件大小（字节）')
    reclassify_existing: bool = Field(description='重复文献是否重新判定')
    parsed_count: int | None = Field(default=None, description='解析出的记录数')
    new_count: int | None = Field(default=None, description='新增题录数')
    duplicate_count: int | None = Field(default=None, description='重复记录数')
    total_count: int | None = Field(default=None, description='需判定的文献数')
    model: str | None = Field(default=None, description='判定使用的模型')
    prompt_version: str | None = Field(default=None, description='提示词版本')
    prompt_tokens: int | None = Field(default=None, description='累计输入token')
    completion_tokens: int | None = Field(default=None, description='累计输出token')
    error_message: str | None = Field(default=None, description='错误信息')
    worker_id: str | None = Field(default=None, description='当前处理的worker')
    heartbeat_time: ApiUtcDateTime | None = Field(default=None, description='worker心跳时间')
    started_time: ApiUtcDateTime | None = Field(default=None, description='开始处理时间')
    finished_time: ApiUtcDateTime | None = Field(default=None, description='结束时间')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: ApiUtcDateTime | None = Field(default=None, description='创建时间')
    pending: int = Field(default=0, description='待判定数')
    running: int = Field(default=0, description='判定中数')
    done: int = Field(default=0, description='已完成数')
    failed: int = Field(default=0, description='失败数')


class WorkerModel(BaseModel):
    """
    在线 worker 模型
    """

    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)

    worker_id: str = Field(description='worker标识')
    model: str | None = Field(default=None, description='使用的模型')
    concurrency: int | None = Field(default=None, description='模型并发数')
    current_job_id: int | None = Field(default=None, description='正在处理的任务ID')
    started_time: ApiUtcDateTime | None = Field(default=None, description='启动时间')
    heartbeat_time: ApiUtcDateTime | None = Field(default=None, description='心跳时间')


class JobOverviewModel(BaseModel):
    """
    任务列表与 worker 在线情况
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    jobs: list[JobModel] = Field(default_factory=list, description='最近的任务')
    workers: list[WorkerModel] = Field(default_factory=list, description='在线的 worker')


class JobCreatedModel(BaseModel):
    """
    任务创建结果
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    job_id: int = Field(description='任务ID')
