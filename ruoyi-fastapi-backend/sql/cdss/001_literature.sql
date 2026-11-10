-- ----------------------------
-- 临床决策支持系统：文献题录与研究类型
-- 表名与病种无关，病种通过 lit_topic 区分（如结直肠腺瘤、产后出血）
-- ----------------------------

-- ----------------------------
-- 1、病种专题表
-- ----------------------------
create table if not exists lit_topic (
    topic_id bigserial not null,
    topic_code varchar(50) not null,
    topic_name varchar(100) not null,
    order_num integer default 0,
    status char(1) default '0',
    create_by varchar(64) default '',
    create_time timestamp(3) with time zone,
    update_by varchar(64) default '',
    update_time timestamp(3) with time zone,
    remark varchar(500) default null,
    primary key (topic_id),
    constraint uk_lit_topic_code unique (topic_code)
);
comment on column lit_topic.topic_id is '病种ID';
comment on column lit_topic.topic_code is '病种编码';
comment on column lit_topic.topic_name is '病种名称';
comment on column lit_topic.order_num is '显示顺序';
comment on column lit_topic.status is '状态（0正常 1停用）';
comment on column lit_topic.create_by is '创建者';
comment on column lit_topic.create_time is '创建时间';
comment on column lit_topic.update_by is '更新者';
comment on column lit_topic.update_time is '更新时间';
comment on column lit_topic.remark is '备注';
comment on table lit_topic is '病种专题表';

-- ----------------------------
-- 2、题录导入批次表
-- ----------------------------
create table if not exists lit_import_batch (
    batch_id bigserial not null,
    topic_id bigint not null,
    source_file varchar(500) not null,
    source_sheet varchar(100) default null,
    file_sha256 char(64) not null,
    total_rows integer default 0,
    imported_rows integer default 0,
    skipped_rows integer default 0,
    import_log text default null,
    create_by varchar(64) default '',
    create_time timestamp(3) with time zone,
    remark varchar(500) default null,
    primary key (batch_id),
    constraint fk_lit_batch_topic foreign key (topic_id) references lit_topic (topic_id)
);
comment on column lit_import_batch.batch_id is '批次ID';
comment on column lit_import_batch.topic_id is '病种ID';
comment on column lit_import_batch.source_file is '来源文件名';
comment on column lit_import_batch.source_sheet is '来源工作表';
comment on column lit_import_batch.file_sha256 is '来源文件SHA-256';
comment on column lit_import_batch.total_rows is '来源总行数';
comment on column lit_import_batch.imported_rows is '导入行数';
comment on column lit_import_batch.skipped_rows is '跳过行数';
comment on column lit_import_batch.import_log is '导入说明（跳过原因等）';
comment on column lit_import_batch.create_by is '创建者';
comment on column lit_import_batch.create_time is '创建时间';
comment on column lit_import_batch.remark is '备注';
comment on table lit_import_batch is '题录导入批次表';

-- ----------------------------
-- 3、文献题录表
-- ----------------------------
create table if not exists lit_record (
    record_id bigserial not null,
    topic_id bigint not null,
    batch_id bigint default null,
    source_id varchar(32) not null,
    source_row integer default null,
    title text not null,
    authors text default null,
    pub_year smallint default null,
    abstract text default null,
    language varchar(10) default null,
    doi varchar(255) default null,
    pmid varchar(20) default null,
    source_db varchar(100) default null,
    pdf_path varchar(500) default null,
    del_flag char(1) default '0',
    create_by varchar(64) default '',
    create_time timestamp(3) with time zone,
    update_by varchar(64) default '',
    update_time timestamp(3) with time zone,
    remark varchar(500) default null,
    primary key (record_id),
    constraint uk_lit_record_source unique (topic_id, source_id),
    constraint fk_lit_record_topic foreign key (topic_id) references lit_topic (topic_id),
    constraint fk_lit_record_batch foreign key (batch_id) references lit_import_batch (batch_id)
);
create index if not exists idx_lit_record_year on lit_record (topic_id, pub_year);
create index if not exists idx_lit_record_doi on lit_record (doi);
create index if not exists idx_lit_record_pmid on lit_record (pmid);
comment on column lit_record.record_id is '题录ID';
comment on column lit_record.topic_id is '病种ID';
comment on column lit_record.batch_id is '导入批次ID';
comment on column lit_record.source_id is '来源编号（导入文件中的ID，同时是全文PDF文件名）';
comment on column lit_record.source_row is '来源文件行号';
comment on column lit_record.title is '标题';
comment on column lit_record.authors is '作者';
comment on column lit_record.pub_year is '发表年份（未知为空）';
comment on column lit_record.abstract is '摘要（无摘要为空）';
comment on column lit_record.language is '语言（zh中文 en英文及其他，按标题字符推断）';
comment on column lit_record.doi is 'DOI';
comment on column lit_record.pmid is 'PubMed ID';
comment on column lit_record.source_db is '检索来源数据库';
comment on column lit_record.pdf_path is '全文PDF相对路径（无全文为空）';
comment on column lit_record.del_flag is '删除标志（0存在 2删除）';
comment on column lit_record.create_by is '创建者';
comment on column lit_record.create_time is '创建时间';
comment on column lit_record.update_by is '更新者';
comment on column lit_record.update_time is '更新时间';
comment on column lit_record.remark is '备注';
comment on table lit_record is '文献题录表';

-- ----------------------------
-- 4、研究类型表（一篇文献可有多次判定，is_current 标记当前采用的结果）
-- ----------------------------
create table if not exists lit_study_design (
    design_id bigserial not null,
    record_id bigint not null,
    design_label varchar(100) not null,
    design_code varchar(50) default null,
    method varchar(20) not null,
    source varchar(200) default null,
    model varchar(100) default null,
    confidence numeric(4, 3) default null,
    rationale text default null,
    is_current boolean not null default true,
    review_status char(1) default '0',
    reviewed_by varchar(64) default null,
    reviewed_time timestamp(3) with time zone,
    create_by varchar(64) default '',
    create_time timestamp(3) with time zone,
    update_by varchar(64) default '',
    update_time timestamp(3) with time zone,
    remark varchar(500) default null,
    primary key (design_id),
    constraint fk_lit_design_record foreign key (record_id) references lit_record (record_id) on delete cascade,
    constraint ck_lit_design_method check (method in ('import', 'llm', 'manual')),
    constraint ck_lit_design_confidence check (confidence is null or (confidence >= 0 and confidence <= 1))
);
create unique index if not exists uk_lit_design_current on lit_study_design (record_id) where is_current;
create index if not exists idx_lit_design_label on lit_study_design (design_label) where is_current;
create index if not exists idx_lit_design_code on lit_study_design (design_code) where is_current;
comment on column lit_study_design.design_id is '研究类型判定ID';
comment on column lit_study_design.record_id is '题录ID';
comment on column lit_study_design.design_label is '研究类型（判定时的原始标签）';
comment on column lit_study_design.design_code is '标准研究类型编码（分类体系确定后填写）';
comment on column lit_study_design.method is '判定方式（import导入 llm模型 manual人工）';
comment on column lit_study_design.source is '判定来源（文件、流程或提示词版本）';
comment on column lit_study_design.model is '模型名称';
comment on column lit_study_design.confidence is '置信度（0-1）';
comment on column lit_study_design.rationale is '判定依据';
comment on column lit_study_design.is_current is '是否当前采用结果';
comment on column lit_study_design.review_status is '审核状态（0待审核 1已确认 2已修改）';
comment on column lit_study_design.reviewed_by is '审核人';
comment on column lit_study_design.reviewed_time is '审核时间';
comment on column lit_study_design.create_by is '创建者';
comment on column lit_study_design.create_time is '创建时间';
comment on column lit_study_design.update_by is '更新者';
comment on column lit_study_design.update_time is '更新时间';
comment on column lit_study_design.remark is '备注';
comment on table lit_study_design is '文献研究类型表';

-- ----------------------------
-- 5、PICO 要素表（来源可为导入的模型抽取结果或人工）
-- ----------------------------
create table if not exists lit_pico (
    pico_id bigserial not null,
    record_id bigint not null,
    participants text default null,
    intervention text default null,
    comparison text default null,
    outcomes text default null,
    method varchar(20) not null,
    source varchar(200) default null,
    create_by varchar(64) default '',
    create_time timestamp(3) with time zone,
    remark varchar(500) default null,
    primary key (pico_id),
    constraint uk_lit_pico_source unique (record_id, source),
    constraint fk_lit_pico_record foreign key (record_id) references lit_record (record_id) on delete cascade,
    constraint ck_lit_pico_method check (method in ('import', 'llm', 'manual'))
);
comment on column lit_pico.pico_id is 'PICO ID';
comment on column lit_pico.record_id is '题录ID';
comment on column lit_pico.participants is '研究对象（P）';
comment on column lit_pico.intervention is '干预/暴露（I）';
comment on column lit_pico.comparison is '对照（C）';
comment on column lit_pico.outcomes is '结局（O）';
comment on column lit_pico.method is '来源方式（import导入 llm模型 manual人工）';
comment on column lit_pico.source is '来源（文件或流程版本）';
comment on column lit_pico.create_by is '创建者';
comment on column lit_pico.create_time is '创建时间';
comment on column lit_pico.remark is '备注';
comment on table lit_pico is '文献PICO要素表';
