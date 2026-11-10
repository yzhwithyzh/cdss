"""
生成向量：段落向量（kb_block_embedding）与题录向量（kb_record_embedding），按索引构建版本（kb_index_build）存放。
向量输入只用原文（raw_only_v1）：段落 = 块原文；题录 = 题名 + 原始摘要。不拼接章节标题、年份、人群等元数据。

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.embed --env dev --target blocks             # 全部已入库文献的检索段落
    python -m tools.kb_pipeline.embed --env dev --target records --ids-file ids.txt
    python -m tools.kb_pipeline.embed --env dev --target records --all-records  # 全部题录（按 token 计费）
同一模型、维度、输入策略复用同一个构建版本；输入未变（input_hash 相同）的不重复调用。
"""

import argparse
import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg2
from dotenv import dotenv_values

from tools.kb_pipeline.ingest import db_dsn
from tools.pdf_parsing.config import BACKEND_ROOT

DEFAULT_MODEL = 'qwen3.7-text-embedding'  # 第0轮嵌入评测：段落召回 R@5 0.825，高于 flash 版 0.65
POLICY = 'raw_only_v1'
BATCH = 10
RETRIES = 5
MAX_INPUT_CHARS = 8000  # 超长输入截断（模型上限 8192 token）
INDEXED_TYPES = ('paragraph', 'list_item', 'table', 'caption')
EXCLUDED_SECTIONS = ('references', 'backmatter', 'title')  # title=题名区（作者、单位、期刊信息）
MIN_CHARS = 10


def load_env(env_name: str) -> dict:
    env: dict = {}
    for name in (f'.env.{env_name}', f'.env.{env_name}.local'):
        if (BACKEND_ROOT / name).is_file():
            env.update(dotenv_values(BACKEND_ROOT / name))
    env.update(os.environ)
    return {k: (v or '').strip().strip('\'"') for k, v in env.items()}


def embed_batch(env: dict, model: str, dim: int, texts: list[str]) -> tuple[list[list[float]], int]:
    body = {'model': model, 'input': texts, 'dimensions': dim, 'encoding_format': 'float'}
    for attempt in range(RETRIES):
        req = urllib.request.Request(
            env['LIT_EMBED_BASE_URL'].rstrip('/') + '/embeddings',
            data=json.dumps(body).encode(),
            method='POST',
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + env['DASHSCOPE_API_KEY']},
        )
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=120).read())
            vecs = [d['embedding'] for d in sorted(r['data'], key=lambda d: d['index'])]
            return vecs, r.get('usage', {}).get('total_tokens', 0)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < RETRIES - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f'{model} HTTP {e.code}: {e.read().decode()[:300]}') from None
        except (OSError, http.client.HTTPException):
            if attempt == RETRIES - 1:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError('unreachable')


def get_build(cur: psycopg2.extensions.cursor, topic_id: int, model: str, dim: int) -> int:
    cur.execute(
        """SELECT build_id FROM kb_index_build WHERE topic_id = %s AND model_id = %s AND dimension = %s
             AND embedding_policy = %s AND status IN ('building', 'ready') ORDER BY build_id DESC LIMIT 1""",
        (topic_id, model, dim, POLICY),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE kb_index_build SET status = 'building' WHERE build_id = %s", (row[0],))
        return row[0]
    cur.execute(
        """INSERT INTO kb_index_build(topic_id, model_id, dimension, embedding_policy)
           VALUES (%s, %s, %s, %s) RETURNING build_id""",
        (topic_id, model, dim, POLICY),
    )
    return cur.fetchone()[0]


def pending(cur: psycopg2.extensions.cursor, target: str, build_id: int, ids: list[str], all_records: bool) -> list:
    """
    待生成的 (对象 ID, 输入文本)；已有且输入未变的跳过
    """
    if target == 'blocks':
        cur.execute(
            """SELECT b.block_id, b.text, e.input_hash FROM kb_block b
               JOIN kb_document_version v ON v.version_id = b.version_id AND v.is_current
               JOIN kb_document d ON d.document_id = v.document_id JOIN lit_record r ON r.record_id = d.record_id
               LEFT JOIN kb_block_embedding e ON e.block_id = b.block_id AND e.build_id = %s
               WHERE b.block_type = ANY(%s) AND b.section <> ALL(%s) AND length(b.text) >= %s
                 AND (%s OR r.source_id = ANY(%s))
               ORDER BY b.block_id""",
            (build_id, list(INDEXED_TYPES), list(EXCLUDED_SECTIONS), MIN_CHARS, not ids, ids),
        )
    else:
        cur.execute(
            """SELECT r.record_id, concat_ws(E'\n', r.title, nullif(r.abstract, '')), e.input_hash FROM lit_record r
               LEFT JOIN kb_record_embedding e ON e.record_id = r.record_id AND e.build_id = %s
               WHERE r.title IS NOT NULL AND (%s OR r.source_id = ANY(%s)
                     OR (NOT %s AND EXISTS (SELECT 1 FROM kb_document d WHERE d.record_id = r.record_id)))
               ORDER BY r.record_id""",
            (build_id, all_records, ids, bool(ids)),
        )
    out = []
    for oid, raw, old_hash in cur.fetchall():
        text = raw[:MAX_INPUT_CHARS]
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != old_hash:
            out.append((oid, text, digest))
    return out


def ensure_index(cur: psycopg2.extensions.cursor, table: str, build_id: int, dim: int) -> None:
    cur.execute(
        f"""CREATE INDEX IF NOT EXISTS {table}_hnsw_b{build_id} ON {table}
            USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE build_id = {build_id}"""
    )


def main() -> None:
    ap = argparse.ArgumentParser(description='生成段落/题录向量')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--target', choices=('blocks', 'records'), required=True)
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--dim', type=int, default=1024)
    ap.add_argument('--ids-file', default='', help='限定题录 source_id；题录目标不给时默认只做已入库全文的题录')
    ap.add_argument('--all-records', action='store_true', help='题录目标：全部题录')
    ap.add_argument('--workers', type=int, default=6, help='并发调用数')
    a = ap.parse_args()
    env = load_env(a.env)
    ids = Path(a.ids_file).read_text(encoding='utf-8').split() if a.ids_file else []
    table = 'kb_block_embedding' if a.target == 'blocks' else 'kb_record_embedding'
    key = 'block_id' if a.target == 'blocks' else 'record_id'
    conn = psycopg2.connect(db_dsn(a.env))
    tokens = done = 0
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT topic_id FROM lit_topic WHERE topic_code = 'crc_adenoma'")
            build_id = get_build(cur, cur.fetchone()[0], a.model, a.dim)
            todo = pending(cur, a.target, build_id, ids, a.all_records)
        print(f'构建 {build_id}（{a.model}, {a.dim} 维, {POLICY}）：待生成 {len(todo)} 条', flush=True)
        batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
        # 接口调用并发；数据库写入在主线程，每批一个事务，中断后可续跑
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futures = {pool.submit(embed_batch, env, a.model, a.dim, [t for _, t, _ in b]): b for b in batches}
            for fut in as_completed(futures):
                batch = futures[fut]
                vecs, used = fut.result()
                tokens += used
                with conn, conn.cursor() as cur:
                    for (oid, _, digest), vec in zip(batch, vecs, strict=True):
                        cur.execute(
                            f"""INSERT INTO {table}(build_id, {key}, input_hash, embedding)
                                VALUES (%s, %s, %s, %s::vector)
                                ON CONFLICT (build_id, {key}) DO UPDATE
                                  SET input_hash = EXCLUDED.input_hash, embedding = EXCLUDED.embedding""",
                            (build_id, oid, digest, json.dumps(vec)),
                        )
                done += len(batch)
                if done % 2000 < BATCH or done == len(todo):
                    print(f'  {done}/{len(todo)}  tokens {tokens}', flush=True)
        with conn, conn.cursor() as cur:
            ensure_index(cur, table, build_id, a.dim)
            cur.execute(
                """UPDATE kb_index_build SET status = 'ready', finish_time = now(),
                          block_count = (SELECT count(*) FROM kb_block_embedding WHERE build_id = %s)
                   WHERE build_id = %s""",
                (build_id, build_id),
            )
    finally:
        conn.close()
    print(f'完成：生成 {done} 条，消耗 {tokens} tokens')


if __name__ == '__main__':
    main()
