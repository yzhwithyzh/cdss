-- ----------------------------
-- 文献导入与研究类型判定任务（由独立 worker 进程执行）
-- ----------------------------

-- ----------------------------
-- 1、任务表：一次 XML 导入 + 研究类型判定
-- 状态：pending 待处理 → parsing 解析中 → running 判定中 → completed 已完成
--       paused 已暂停 / cancelled 已取消 / failed 失败（解析失败等）
-- ----------------------------
create table if not exists lit_job (
    job_id bigserial not null,
    topic_id bigint not null,
    batch_id bigint default null,
    status varchar(20) not null default 'pending',
    source_file varchar(500) not null,
    file_path varchar(1000) not null,
    file_sha256 char(64) not null,
    file_size bigint default 0,
    reclassify_existing boolean not null default false,
    parsed_count integer default 0,
    new_count integer default 0,
    duplicate_count integer default 0,
    total_count integer default 0,
    model varchar(100) default null,
    prompt_version varchar(50) default null,
    prompt_tokens bigint default 0,
    completion_tokens bigint default 0,
    error_message text default null,
    worker_id varchar(100) default null,
    heartbeat_time timestamp(3) with time zone,
    started_time timestamp(3) with time zone,
    finished_time timestamp(3) with time zone,
    create_by varchar(64) default '',
    create_time timestamp(3) with time zone,
    update_by varchar(64) default '',
    update_time timestamp(3) with time zone,
    remark varchar(500) default null,
    primary key (job_id),
    constraint fk_lit_job_topic foreign key (topic_id) references lit_topic (topic_id),
    constraint fk_lit_job_batch foreign key (batch_id) references lit_import_batch (batch_id),
    constraint ck_lit_job_status check (status in ('pending', 'parsing', 'running', 'paused', 'completed', 'cancelled', 'failed'))
);
create index if not exists idx_lit_job_status on lit_job (status);
comment on column lit_job.job_id is '任务ID';
comment on column lit_job.topic_id is '病种ID';
comment on column lit_job.batch_id is '对应的题录导入批次ID';
comment on column lit_job.status is '状态（pending待处理 parsing解析中 running判定中 paused已暂停 completed已完成 cancelled已取消 failed失败）';
comment on column lit_job.source_file is '上传文件名';
comment on column lit_job.file_path is '服务器保存路径';
comment on column lit_job.file_sha256 is '文件SHA-256';
comment on column lit_job.file_size is '文件大小（字节）';
comment on column lit_job.reclassify_existing is '与已有文献重复时是否也重新判定';
comment on column lit_job.parsed_count is '解析出的记录数';
comment on column lit_job.new_count is '新增题录数';
comment on column lit_job.duplicate_count is '重复记录数（库内已有或文件内重复）';
comment on column lit_job.total_count is '需判定的文献数';
comment on column lit_job.model is '判定使用的模型';
comment on column lit_job.prompt_version is '提示词版本';
comment on column lit_job.prompt_tokens is '累计输入token';
comment on column lit_job.completion_tokens is '累计输出token';
comment on column lit_job.error_message is '错误信息';
comment on column lit_job.worker_id is '当前处理的worker标识';
comment on column lit_job.heartbeat_time is 'worker心跳时间';
comment on column lit_job.started_time is '开始处理时间';
comment on column lit_job.finished_time is '结束时间';
comment on column lit_job.create_by is '创建者';
comment on column lit_job.create_time is '创建时间';
comment on column lit_job.update_by is '更新者';
comment on column lit_job.update_time is '更新时间';
comment on column lit_job.remark is '备注';
comment on table lit_job is '文献导入与研究类型判定任务表';

-- ----------------------------
-- 2、任务明细表：每篇需判定的文献一行
-- 状态：pending 待判定 / running 判定中 / done 已完成 / failed 失败
-- ----------------------------
create table if not exists lit_job_item (
    item_id bigserial not null,
    job_id bigint not null,
    record_id bigint not null,
    status varchar(20) not null default 'pending',
    attempts integer not null default 0,
    last_error text default null,
    locked_by varchar(100) default null,
    locked_time timestamp(3) with time zone,
    finished_time timestamp(3) with time zone,
    design_id bigint default null,
    prompt_tokens integer default 0,
    completion_tokens integer default 0,
    primary key (item_id),
    constraint uk_lit_job_item unique (job_id, record_id),
    constraint fk_lit_job_item_job foreign key (job_id) references lit_job (job_id) on delete cascade,
    constraint fk_lit_job_item_record foreign key (record_id) references lit_record (record_id) on delete cascade,
    constraint ck_lit_job_item_status check (status in ('pending', 'running', 'done', 'failed'))
);
create index if not exists idx_lit_job_item_status on lit_job_item (job_id, status);
comment on column lit_job_item.item_id is '明细ID';
comment on column lit_job_item.job_id is '任务ID';
comment on column lit_job_item.record_id is '题录ID';
comment on column lit_job_item.status is '状态（pending待判定 running判定中 done已完成 failed失败）';
comment on column lit_job_item.attempts is '已尝试次数';
comment on column lit_job_item.last_error is '最近一次错误';
comment on column lit_job_item.locked_by is '处理中的worker标识';
comment on column lit_job_item.locked_time is '领取时间';
comment on column lit_job_item.finished_time is '完成时间';
comment on column lit_job_item.design_id is '写入的研究类型判定ID';
comment on column lit_job_item.prompt_tokens is '输入token';
comment on column lit_job_item.completion_tokens is '输出token';
comment on table lit_job_item is '文献判定任务明细表';

-- ----------------------------
-- 3、worker 心跳表：页面据此提示是否有 worker 在线
-- ----------------------------
create table if not exists lit_worker (
    worker_id varchar(100) not null,
    hostname varchar(100) default null,
    pid integer default null,
    model varchar(100) default null,
    concurrency integer default null,
    current_job_id bigint default null,
    started_time timestamp(3) with time zone,
    heartbeat_time timestamp(3) with time zone,
    primary key (worker_id)
);
comment on column lit_worker.worker_id is 'worker标识（主机名:名称）';
comment on column lit_worker.hostname is '主机名';
comment on column lit_worker.pid is '进程号';
comment on column lit_worker.model is '使用的模型';
comment on column lit_worker.concurrency is '模型并发数';
comment on column lit_worker.current_job_id is '正在处理的任务ID';
comment on column lit_worker.started_time is '启动时间';
comment on column lit_worker.heartbeat_time is '心跳时间';
comment on table lit_worker is '文献判定worker心跳表';

-- ----------------------------
-- 4、菜单权限：导入与任务管理（挂在“文献题录”下）
-- ----------------------------
insert into sys_menu (menu_id, menu_name, parent_id, order_num, path, component, query, route_name, is_frame, is_cache, menu_type, visible, status, perms, icon, create_by, create_time, remark)
values
    (2006, '导入判定', 2001, 5, '', null, null, '', 1, 0, 'F', '0', '0', 'literature:job:add', '#', 'admin', now(), ''),
    (2007, '任务管理', 2001, 6, '', null, null, '', 1, 0, 'F', '0', '0', 'literature:job:edit', '#', 'admin', now(), '')
on conflict (menu_id) do nothing;

select setval('sys_menu_menu_id_seq', greatest((select max(menu_id) from sys_menu), 2007));
