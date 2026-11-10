"""
文献导入与研究类型判定任务的执行器

任务状态全部保存在 PostgreSQL 中：
- 领取任务、领取明细均使用 FOR UPDATE SKIP LOCKED，可同时运行多个 worker；
- worker 定期写心跳，心跳超时的任务可被其他 worker 接管，未完成的明细会重新排队；
- 解析与入库在一个事务内完成，中途崩溃会整体回滚后重新解析。
"""

import asyncio
import json
import os
import socket
from pathlib import Path

import asyncpg
from loguru import logger

from module_literature.worker.config import WorkerSettings
from module_literature.worker.llm_client import FatalLlmError, LlmClient
from module_literature.worker.prompt import CategorySet, build_system_prompt, build_user_prompt, parse_result
from module_literature.worker.xml_parser import iter_records, normalize_doi, normalize_title

OPERATOR = 'worker'
INSERT_CHUNK = 2000
STATUS_CHECK_SECONDS = 2.0


class JobStopped(Exception):
    """
    任务被暂停、取消或被其他 worker 接管
    """


class LiteratureWorker:
    """
    文献判定 worker
    """

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.categories = CategorySet.load()
        self.system_prompt = build_system_prompt(self.categories)
        self.pool: asyncpg.Pool | None = None
        self.llm: LlmClient | None = None
        self.current_job_id: int | None = None
        self.stopping = asyncio.Event()

    # ------------------------------------------------------------------ 生命周期

    async def run(self) -> None:
        """
        主循环：领取任务并处理，无任务时按间隔轮询
        """
        self.pool = await asyncpg.create_pool(self.settings.db_dsn, min_size=2, max_size=self.settings.concurrency + 4)
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            f'worker {self.settings.worker_id} 已启动：模型 {self.settings.llm_model}，'
            f'并发 {self.settings.concurrency}，分类体系 {self.categories.version}'
        )
        try:
            while not self.stopping.is_set():
                job = await self._claim_job()
                if job is None:
                    await self._sleep(self.settings.poll_interval)
                    continue
                self.current_job_id = job['job_id']
                try:
                    await self._process_job(job)
                except JobStopped as e:
                    logger.info(f'任务#{job["job_id"]} 停止处理：{e}')
                finally:
                    self.current_job_id = None
        finally:
            heartbeat.cancel()
            if self.llm:
                await self.llm.close()
            await self.pool.execute('delete from lit_worker where worker_id = $1', self.settings.worker_id)
            await self.pool.close()
            logger.info('worker 已退出')

    def stop(self) -> None:
        self.stopping.set()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _heartbeat_loop(self) -> None:
        started = True
        while True:
            try:
                await self.pool.execute(
                    """insert into lit_worker
                       (worker_id, hostname, pid, model, concurrency, current_job_id, started_time, heartbeat_time)
                       values ($1, $2, $3, $4, $5, $6, now(), now())
                       on conflict (worker_id) do update set
                         hostname = excluded.hostname, pid = excluded.pid, model = excluded.model,
                         concurrency = excluded.concurrency, current_job_id = excluded.current_job_id,
                         started_time = case when $7 then now() else lit_worker.started_time end,
                         heartbeat_time = now()""",
                    self.settings.worker_id,
                    socket.gethostname(),
                    os.getpid(),
                    self.settings.llm_model,
                    self.settings.concurrency,
                    self.current_job_id,
                    started,
                )
                started = False
                if self.current_job_id:
                    await self.pool.execute(
                        'update lit_job set heartbeat_time = now() where job_id = $1 and worker_id = $2',
                        self.current_job_id,
                        self.settings.worker_id,
                    )
            except Exception as e:  # 心跳失败不应终止 worker
                logger.warning(f'心跳写入失败：{e}')
            await asyncio.sleep(self.settings.heartbeat_interval)

    # ------------------------------------------------------------------ 任务领取

    async def _claim_job(self) -> asyncpg.Record | None:
        """
        领取一个任务：待处理任务，或心跳超时/本 worker 上次未完成的任务
        """
        async with self.pool.acquire() as conn, conn.transaction():
            job = await conn.fetchrow(
                """update lit_job set
                     worker_id = $1,
                     heartbeat_time = now(),
                     started_time = coalesce(started_time, now()),
                     status = case when status = 'pending' then 'parsing' else status end,
                     model = case when status in ('pending', 'parsing') then $3 else model end,
                     prompt_version = case when status in ('pending', 'parsing') then $4 else prompt_version end,
                     update_time = now()
                   where job_id = (
                     select job_id from lit_job
                     where status = 'pending'
                        or (status in ('parsing', 'running')
                            and (worker_id is null or worker_id = $1
                                 or heartbeat_time < now() - make_interval(secs => $2::double precision)))
                     order by job_id
                     limit 1
                     for update skip locked
                   )
                   returning *""",
                self.settings.worker_id,
                float(self.settings.stale_seconds),
                self.settings.llm_model,
                self.categories.version,
            )
            if job and job['status'] == 'running':
                # 接管：上一个 worker 未完成的明细重新排队
                reset = await conn.execute(
                    """update lit_job_item set status = 'pending', locked_by = null, locked_time = null
                       where job_id = $1 and status = 'running'""",
                    job['job_id'],
                )
                logger.info(f'接管任务#{job["job_id"]}，重新排队：{reset}')
        return job

    async def _assert_job_active(self, job_id: int) -> None:
        row = await self.pool.fetchrow('select status, worker_id from lit_job where job_id = $1', job_id)
        if row is None:
            raise JobStopped('任务已删除')
        if row['worker_id'] != self.settings.worker_id:
            raise JobStopped('任务已被其他 worker 接管')
        if row['status'] not in ('parsing', 'running'):
            raise JobStopped(f'任务状态为 {row["status"]}')

    # ------------------------------------------------------------------ 处理

    async def _process_job(self, job: asyncpg.Record) -> None:
        job_id = job['job_id']
        if job['status'] == 'parsing':
            try:
                await self._parse_and_import(job)
            except JobStopped:
                raise
            except Exception as e:
                logger.exception(f'任务#{job_id} 解析失败')
                await self.pool.execute(
                    """update lit_job set status = 'failed', error_message = $2, worker_id = null,
                       finished_time = now(), update_time = now() where job_id = $1""",
                    job_id,
                    f'XML解析或入库失败：{e}',
                )
                return
        await self._classify_items(job_id)

    async def _parse_and_import(self, job: asyncpg.Record) -> None:
        """
        解析 XML 并写入题录与任务明细（单事务）
        """
        job_id, topic_id = job['job_id'], job['topic_id']
        xml_path = Path(job['file_path'])
        logger.info(f'任务#{job_id} 开始解析 {job["source_file"]}')

        keys = await self._load_existing_keys(topic_id)
        new_rows, reclassify, stats = await asyncio.to_thread(self._scan_xml, job_id, topic_id, xml_path, keys)
        if not job['reclassify_existing']:
            reclassify = set()

        async with self.pool.acquire() as conn, conn.transaction():
            await self._assert_job_active(job_id)
            batch_id = await conn.fetchval(
                """insert into lit_import_batch
                   (topic_id, source_file, source_sheet, file_sha256, total_rows, imported_rows, skipped_rows,
                    import_log, create_by, create_time, remark)
                   values ($1, $2, null, $3, $4, $5, $6, $7, $8, now(), $9) returning batch_id""",
                topic_id,
                job['source_file'],
                job['file_sha256'],
                stats['parsed'],
                len(new_rows),
                stats['parsed'] - len(new_rows),
                f'重复 {stats["duplicate"]} 条（库内已有或文件内重复），无标题 {stats["no_title"]} 条',
                OPERATOR,
                f'XML导入任务#{job_id}',
            )
            for start in range(0, len(new_rows), INSERT_CHUNK):
                await conn.executemany(
                    """insert into lit_record
                       (topic_id, batch_id, source_id, source_row, title, authors, pub_year, abstract, language,
                        doi, pmid, create_by, create_time)
                       values ($1, $11, $2, $3, $4, $5, $6, $7, $8, $9, $10, $12, now())""",
                    [(*row, batch_id, OPERATOR) for row in new_rows[start : start + INSERT_CHUNK]],
                )
            await conn.execute(
                """insert into lit_job_item (job_id, record_id)
                   select $1::bigint, record_id from lit_record where batch_id = $2
                   union
                   select $1::bigint, unnest($3::bigint[])
                   on conflict do nothing""",
                job_id,
                batch_id,
                list(reclassify),
            )
            total = await conn.fetchval('select count(*) from lit_job_item where job_id = $1', job_id)
            await conn.execute(
                """update lit_job set status = 'running', batch_id = $2, parsed_count = $3, new_count = $4,
                   duplicate_count = $5, total_count = $6, error_message = null, update_time = now()
                   where job_id = $1""",
                job_id,
                batch_id,
                stats['parsed'],
                len(new_rows),
                stats['duplicate'],
                total,
            )
        logger.info(
            f'任务#{job_id} 解析完成：{stats["parsed"]} 条，新增 {len(new_rows)}，重复 {stats["duplicate"]}，'
            f'待判定 {total}'
        )

    async def _load_existing_keys(self, topic_id: int) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """
        读取病种下已有题录的 DOI、PMID、归一化标题，用于去重

        :param topic_id: 病种ID
        :return: (DOI→题录ID, PMID→题录ID, 标题→题录ID)
        """
        rows = await self.pool.fetch(
            "select record_id, doi, pmid, title from lit_record where topic_id = $1 and del_flag = '0'", topic_id
        )
        by_doi, by_pmid, by_title = {}, {}, {}
        for row in rows:
            if doi := normalize_doi(row['doi']):
                by_doi.setdefault(doi, row['record_id'])
            if row['pmid']:
                by_pmid.setdefault(row['pmid'], row['record_id'])
            if title_key := normalize_title(row['title']):
                by_title.setdefault(title_key, row['record_id'])
        return by_doi, by_pmid, by_title

    @staticmethod
    def _scan_xml(
        job_id: int, topic_id: int, xml_path: Path, keys: tuple[dict[str, int], dict[str, int], dict[str, int]]
    ) -> tuple[list[tuple], set[int], dict[str, int]]:
        """
        流式解析 XML 并去重（库内已有、文件内重复按 DOI / PMID / 归一化标题判断）

        :return: (新题录行, 库内已有的重复题录ID, 统计)
        """
        by_doi, by_pmid, by_title = keys
        new_rows: list[tuple] = []
        existing_hits: set[int] = set()
        stats = {'parsed': 0, 'duplicate': 0, 'no_title': 0}
        seen_doi, seen_pmid, seen_title, used_ids = set(), set(), set(), set()
        for record in iter_records(xml_path):
            stats['parsed'] += 1
            if not record.title:
                stats['no_title'] += 1
                continue
            title_key = normalize_title(record.title)
            existing_id = (
                (record.doi and by_doi.get(record.doi))
                or (record.pmid and by_pmid.get(record.pmid))
                or (title_key and by_title.get(title_key))
            )
            if existing_id:
                stats['duplicate'] += 1
                existing_hits.add(existing_id)
                continue
            if (
                (record.doi and record.doi in seen_doi)
                or (record.pmid and record.pmid in seen_pmid)
                or (title_key and title_key in seen_title)
            ):
                stats['duplicate'] += 1
                continue
            seen_doi.add(record.doi)
            seen_pmid.add(record.pmid)
            seen_title.add(title_key)
            source_id = f'J{job_id}-{record.rec_number or record.seq}'
            if source_id in used_ids:
                source_id = f'J{job_id}-#{record.seq}'
            used_ids.add(source_id)
            new_rows.append(
                (
                    topic_id,
                    source_id,
                    record.seq,
                    record.title,
                    record.authors,
                    record.pub_year,
                    record.abstract,
                    record.language,
                    record.doi,
                    record.pmid,
                )
            )
        return new_rows, existing_hits, stats

    async def _classify_items(self, job_id: int) -> None:
        """
        并发判定任务中的待处理明细，直到全部完成或任务被暂停/取消
        """
        if self.llm is None:
            try:
                self.llm = LlmClient(self.settings)
            except FatalLlmError as e:
                await self._pause_job(job_id, f'模型配置错误：{e}')
                return
        in_flight: set[asyncio.Task] = set()
        fatal: list[str] = []
        loop = asyncio.get_running_loop()
        last_check = 0.0
        try:
            while not self.stopping.is_set() and not fatal:
                if loop.time() - last_check > STATUS_CHECK_SECONDS:
                    await self._assert_job_active(job_id)
                    last_check = loop.time()
                free = self.settings.concurrency - len(in_flight)
                if free > 0:
                    for item in await self._claim_items(job_id, free):
                        in_flight.add(asyncio.create_task(self._process_item(job_id, item, fatal)))
                if not in_flight:
                    break
                _, in_flight = await asyncio.wait(in_flight, timeout=1, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
        if fatal:
            await self._pause_job(job_id, fatal[0])
            return
        if self.stopping.is_set():
            return
        await self._finish_job_if_done(job_id)

    async def _claim_items(self, job_id: int, limit: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            """update lit_job_item set status = 'running', locked_by = $2, locked_time = now(),
                 attempts = attempts + 1
               where item_id in (
                 select item_id from lit_job_item where job_id = $1 and status = 'pending'
                 order by item_id limit $3 for update skip locked
               )
               returning item_id, record_id, attempts""",
            job_id,
            self.settings.worker_id,
            limit,
        )

    async def _process_item(self, job_id: int, item: asyncpg.Record, fatal: list[str]) -> None:
        try:
            await self._classify_one(job_id, item, fatal)
        except Exception as e:
            # 兜底：写库等意外错误也要释放明细，避免其长期停留在“判定中”
            logger.exception(f'任务#{job_id} 题录{item["record_id"]} 处理异常')
            try:
                await self._release_item(item, f'{type(e).__name__}: {e}')
            except Exception:
                logger.exception(f'任务#{job_id} 题录{item["record_id"]} 释放失败，将在任务被接管时重新排队')

    async def _classify_one(self, job_id: int, item: asyncpg.Record, fatal: list[str]) -> None:
        record = await self.pool.fetchrow(
            'select title, abstract from lit_record where record_id = $1', item['record_id']
        )
        try:
            if record is None:
                raise ValueError('题录不存在')
            reply = await self.llm.complete(self.system_prompt, build_user_prompt(record['title'], record['abstract']))
            result, label, out_of_list = parse_result(reply.content, self.categories)
        except FatalLlmError as e:
            fatal.append(f'模型调用失败，任务已暂停：{e}')
            await self._release_item(item, str(e), count_attempt=False)
            return
        except Exception as e:  # 单篇失败只记录，不影响其他文献
            logger.warning(f'任务#{job_id} 题录{item["record_id"]} 判定失败（第{item["attempts"]}次）：{e}')
            await self._release_item(item, f'{type(e).__name__}: {e}')
            return
        await self._save_result(job_id, item, result, label, out_of_list, reply)

    async def _release_item(self, item: asyncpg.Record, error: str, count_attempt: bool = True) -> None:
        exhausted = count_attempt and item['attempts'] >= self.settings.max_attempts
        await self.pool.execute(
            """update lit_job_item set status = $2::varchar, last_error = $3, locked_by = null, locked_time = null,
                 attempts = attempts - $4,
                 finished_time = case when $2::varchar = 'failed' then now() else null end
               where item_id = $1""",
            item['item_id'],
            'failed' if exhausted else 'pending',
            error[:2000],
            0 if count_attempt else 1,
        )

    async def _save_result(self, job_id, item, result, label, out_of_list, reply) -> None:  # noqa: ANN001
        record_id = item['record_id']
        source = f'任务#{job_id} · {self.categories.version}'
        rationale_parts = [result.reason, f'原文设计描述：{result.study_design_text}']
        if out_of_list:
            rationale_parts.append(f'模型输出类别“{result.category}”不在分类体系中，已归为“{label}”')
        rationale = '\n'.join(part for part in rationale_parts if part)
        async with self.pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                """select design_id, method, review_status from lit_study_design
                   where record_id = $1 and is_current for update""",
                record_id,
            )
            # 人工结果或已审核的结果不被模型覆盖，模型结果仅作为历史记录保留
            replace = current is None or (current['method'] in ('import', 'llm') and current['review_status'] == '0')
            if replace and current:
                await conn.execute(
                    """update lit_study_design set is_current = false, update_by = $2, update_time = now()
                       where design_id = $1""",
                    current['design_id'],
                    OPERATOR,
                )
            design_id = await conn.fetchval(
                """insert into lit_study_design
                   (record_id, design_label, method, source, model, confidence, rationale, is_current, review_status,
                    create_by, create_time, update_by, update_time, remark)
                   values ($1, $2, 'llm', $3, $4, $5::double precision, $6, $7, '0', $8, now(), $8, now(), $9)
                   returning design_id""",
                record_id,
                label,
                source,
                self.settings.llm_model,
                result.confidence,
                rationale,
                replace,
                OPERATOR,
                None if replace else '已有人工审核结果，模型结果仅作参考',
            )
            await conn.execute(
                """update lit_job_item set status = 'done', design_id = $2, prompt_tokens = $3,
                     completion_tokens = $4, last_error = null, locked_by = null, finished_time = now()
                   where item_id = $1""",
                item['item_id'],
                design_id,
                reply.prompt_tokens,
                reply.completion_tokens,
            )
            await conn.execute(
                """update lit_job set prompt_tokens = prompt_tokens + $2,
                     completion_tokens = completion_tokens + $3 where job_id = $1""",
                job_id,
                reply.prompt_tokens,
                reply.completion_tokens,
            )

    async def _pause_job(self, job_id: int, message: str) -> None:
        logger.error(f'任务#{job_id} 暂停：{message}')
        await self.pool.execute(
            """update lit_job set status = 'paused', error_message = $2, worker_id = null, update_time = now()
               where job_id = $1 and status = 'running'""",
            job_id,
            message[:2000],
        )

    async def _finish_job_if_done(self, job_id: int) -> None:
        counts = dict(
            await self.pool.fetch('select status, count(*) from lit_job_item where job_id = $1 group by status', job_id)
        )
        if counts.get('pending') or counts.get('running'):
            return
        await self.pool.execute(
            """update lit_job set status = 'completed', worker_id = null, finished_time = now(), update_time = now()
               where job_id = $1 and status = 'running'""",
            job_id,
        )
        logger.info(f'任务#{job_id} 完成：{json.dumps(counts, ensure_ascii=False)}')
