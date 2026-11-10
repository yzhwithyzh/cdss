from sqlalchemy import CHAR, BigInteger, Boolean, Column, Integer, Numeric, SmallInteger, String, Text

from common.mixin import AuditTimeMixin, CreateTimeMixin
from common.types import DbUtcDateTime
from config.database import Base


class LitTopic(AuditTimeMixin, Base):
    """
    病种专题表
    """

    __tablename__ = 'lit_topic'
    __table_args__ = {'comment': '病种专题表'}

    topic_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='病种ID')
    topic_code = Column(String(50), nullable=False, unique=True, comment='病种编码')
    topic_name = Column(String(100), nullable=False, comment='病种名称')
    order_num = Column(Integer, server_default='0', comment='显示顺序')
    status = Column(CHAR(1), server_default='0', comment='状态（0正常 1停用）')
    create_by = Column(String(64), server_default="''", comment='创建者')
    update_by = Column(String(64), server_default="''", comment='更新者')
    remark = Column(String(500), comment='备注')


class LitRecord(AuditTimeMixin, Base):
    """
    文献题录表
    """

    __tablename__ = 'lit_record'
    __table_args__ = {'comment': '文献题录表'}

    record_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='题录ID')
    topic_id = Column(BigInteger, nullable=False, comment='病种ID')
    batch_id = Column(BigInteger, comment='导入批次ID')
    source_id = Column(String(32), nullable=False, comment='来源编号')
    source_row = Column(Integer, comment='来源文件行号')
    title = Column(Text, nullable=False, comment='标题')
    authors = Column(Text, comment='作者')
    pub_year = Column(SmallInteger, comment='发表年份')
    abstract = Column(Text, comment='摘要')
    language = Column(String(10), comment='语言')
    doi = Column(String(255), comment='DOI')
    pmid = Column(String(20), comment='PubMed ID')
    source_db = Column(String(100), comment='检索来源数据库')
    pdf_path = Column(String(500), comment='全文PDF相对路径')
    del_flag = Column(CHAR(1), server_default='0', comment='删除标志（0存在 2删除）')
    create_by = Column(String(64), server_default="''", comment='创建者')
    update_by = Column(String(64), server_default="''", comment='更新者')
    remark = Column(String(500), comment='备注')


class LitStudyDesign(AuditTimeMixin, Base):
    """
    文献研究类型表
    """

    __tablename__ = 'lit_study_design'
    __table_args__ = {'comment': '文献研究类型表'}

    design_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='研究类型判定ID')
    record_id = Column(BigInteger, nullable=False, comment='题录ID')
    design_label = Column(String(100), nullable=False, comment='研究类型')
    design_code = Column(String(50), comment='标准研究类型编码')
    method = Column(String(20), nullable=False, comment='判定方式（import导入 llm模型 manual人工）')
    source = Column(String(200), comment='判定来源')
    model = Column(String(100), comment='模型名称')
    confidence = Column(Numeric(4, 3), comment='置信度（0-1）')
    rationale = Column(Text, comment='判定依据')
    is_current = Column(Boolean, nullable=False, server_default='true', comment='是否当前采用结果')
    review_status = Column(CHAR(1), server_default='0', comment='审核状态（0待审核 1已确认 2已修改）')
    reviewed_by = Column(String(64), comment='审核人')
    reviewed_time = Column(DbUtcDateTime(), comment='审核时间')
    create_by = Column(String(64), server_default="''", comment='创建者')
    update_by = Column(String(64), server_default="''", comment='更新者')
    remark = Column(String(500), comment='备注')


class LitPico(CreateTimeMixin, Base):
    """
    文献PICO要素表
    """

    __tablename__ = 'lit_pico'
    __table_args__ = {'comment': '文献PICO要素表'}

    pico_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='PICO ID')
    record_id = Column(BigInteger, nullable=False, comment='题录ID')
    participants = Column(Text, comment='研究对象（P）')
    intervention = Column(Text, comment='干预/暴露（I）')
    comparison = Column(Text, comment='对照（C）')
    outcomes = Column(Text, comment='结局（O）')
    method = Column(String(20), nullable=False, comment='来源方式')
    source = Column(String(200), comment='来源')
    create_by = Column(String(64), server_default="''", comment='创建者')
    remark = Column(String(500), comment='备注')


class LitJob(AuditTimeMixin, Base):
    """
    文献导入与研究类型判定任务表（由独立 worker 执行）
    """

    __tablename__ = 'lit_job'
    __table_args__ = {'comment': '文献导入与研究类型判定任务表'}

    job_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='任务ID')
    topic_id = Column(BigInteger, nullable=False, comment='病种ID')
    batch_id = Column(BigInteger, comment='题录导入批次ID')
    status = Column(String(20), nullable=False, server_default='pending', comment='状态')
    source_file = Column(String(500), nullable=False, comment='上传文件名')
    file_path = Column(String(1000), nullable=False, comment='服务器保存路径')
    file_sha256 = Column(CHAR(64), nullable=False, comment='文件SHA-256')
    file_size = Column(BigInteger, server_default='0', comment='文件大小（字节）')
    reclassify_existing = Column(Boolean, nullable=False, server_default='false', comment='重复文献是否重新判定')
    parsed_count = Column(Integer, server_default='0', comment='解析出的记录数')
    new_count = Column(Integer, server_default='0', comment='新增题录数')
    duplicate_count = Column(Integer, server_default='0', comment='重复记录数')
    total_count = Column(Integer, server_default='0', comment='需判定的文献数')
    model = Column(String(100), comment='判定使用的模型')
    prompt_version = Column(String(50), comment='提示词版本')
    prompt_tokens = Column(BigInteger, server_default='0', comment='累计输入token')
    completion_tokens = Column(BigInteger, server_default='0', comment='累计输出token')
    error_message = Column(Text, comment='错误信息')
    worker_id = Column(String(100), comment='当前处理的worker标识')
    heartbeat_time = Column(DbUtcDateTime(), comment='worker心跳时间')
    started_time = Column(DbUtcDateTime(), comment='开始处理时间')
    finished_time = Column(DbUtcDateTime(), comment='结束时间')
    create_by = Column(String(64), server_default="''", comment='创建者')
    update_by = Column(String(64), server_default="''", comment='更新者')
    remark = Column(String(500), comment='备注')


class LitJobItem(Base):
    """
    文献判定任务明细表
    """

    __tablename__ = 'lit_job_item'
    __table_args__ = {'comment': '文献判定任务明细表'}

    item_id = Column(BigInteger, primary_key=True, autoincrement=True, comment='明细ID')
    job_id = Column(BigInteger, nullable=False, comment='任务ID')
    record_id = Column(BigInteger, nullable=False, comment='题录ID')
    status = Column(String(20), nullable=False, server_default='pending', comment='状态')
    attempts = Column(Integer, nullable=False, server_default='0', comment='已尝试次数')
    last_error = Column(Text, comment='最近一次错误')
    locked_by = Column(String(100), comment='处理中的worker标识')
    locked_time = Column(DbUtcDateTime(), comment='领取时间')
    finished_time = Column(DbUtcDateTime(), comment='完成时间')
    design_id = Column(BigInteger, comment='写入的研究类型判定ID')
    prompt_tokens = Column(Integer, server_default='0', comment='输入token')
    completion_tokens = Column(Integer, server_default='0', comment='输出token')


class LitWorker(Base):
    """
    文献判定 worker 心跳表
    """

    __tablename__ = 'lit_worker'
    __table_args__ = {'comment': '文献判定worker心跳表'}

    worker_id = Column(String(100), primary_key=True, comment='worker标识')
    hostname = Column(String(100), comment='主机名')
    pid = Column(Integer, comment='进程号')
    model = Column(String(100), comment='使用的模型')
    concurrency = Column(Integer, comment='模型并发数')
    current_job_id = Column(BigInteger, comment='正在处理的任务ID')
    started_time = Column(DbUtcDateTime(), comment='启动时间')
    heartbeat_time = Column(DbUtcDateTime(), comment='心跳时间')
