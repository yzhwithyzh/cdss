"""
结构化抽取（试跑版）：kb_block → LLM → 程序校验 →（一轮修复）→ kb_extraction_run / kb_validator_issue

用法（ruoyi-fastapi-backend 目录下）：
    python -m tools.kb_pipeline.extract --env dev --ids-file ids.txt --dry-run   # 只估算输入 token，不调模型
    python -m tools.kb_pipeline.extract --env dev --ids 2800,41115              # 实际抽取
    python -m tools.kb_pipeline.extract --env dev --ids 2800 --mock-dir dir     # 用 dir/<ID>.json 代替模型回复（测试）
缓存键 = 文件哈希 + 解析器版本 + 模型 + schema + 提示词；命中缓存且成功的不再调用模型（--force 强制重跑）。
"""

import argparse
import base64
import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import openai
import psycopg2
from dotenv import dotenv_values

from tools.kb_pipeline.ingest import db_dsn
from tools.kb_pipeline.prompts import (
    PROMPT_VERSION,
    SKELETON_NOTE,
    chunk_prompt,
    compact_schema,
    merge_chunk,
    system_prompt,
    user_prompt,
)
from tools.kb_pipeline.validators import Issue, validate
from tools.pdf_parsing.config import BACKEND_ROOT

SCHEMA_VERSION = 'kb-extract-0.1'
EXCLUDE_SECTIONS = ('references',)
MAX_OUTPUT_TOKENS = 32768
REPAIR_ROUNDS = 1
CHUNK_TOKENS = 6000  # 分段抽取时每段原文的估算 token 上限
TRANSIENT = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)


@dataclass
class LlmSettings:
    base_url: str
    api_key: str
    model: str
    timeout: float
    json_mode: bool
    concurrency: int
    price_in: float  # 元 / 百万输入 token（未配置为 0）
    price_out: float
    source: str = 'env'  # env：.env 配置；platform：系统“AI 模型管理”
    reasoning: bool = False  # 抽取一律不开深度思考

    @classmethod
    def load(cls, env_name: str) -> 'LlmSettings':
        """
        模型配置来源：LIT_EXTRACT_API_KEY 已配置时用 .env；否则读取“AI 模型管理”（ai_models）中启用的模型，
        LIT_EXTRACT_MODEL_CODE 可指定 model_code，未指定时取排序最前的启用模型
        """
        env: dict = {}
        for name in (f'.env.{env_name}', f'.env.{env_name}.local'):
            if (BACKEND_ROOT / name).is_file():
                env.update(dotenv_values(BACKEND_ROOT / name))
        env.update(os.environ)

        def get(key: str, default: str = '') -> str:
            return (env.get(key) or default).strip().strip('\'"')

        common = {
            'timeout': float(get('LIT_EXTRACT_TIMEOUT', '600')),
            'json_mode': get('LIT_LLM_JSON_MODE', 'true').lower() == 'true',
            'concurrency': max(1, int(get('LIT_EXTRACT_CONCURRENCY', '4'))),
            'price_in': float(get('LIT_EXTRACT_PRICE_IN', '0')),
            'price_out': float(get('LIT_EXTRACT_PRICE_OUT', '0')),
        }
        if get('LIT_EXTRACT_API_KEY'):
            return cls(
                base_url=get('LIT_EXTRACT_BASE_URL') or get('LIT_LLM_BASE_URL'),
                api_key=get('LIT_EXTRACT_API_KEY'),
                model=get('LIT_EXTRACT_MODEL') or get('LIT_LLM_MODEL'),
                **common,
            )
        base_url, api_key, model = platform_model(env_name, get('JWT_SECRET_KEY'), get('LIT_EXTRACT_MODEL_CODE'))
        return cls(base_url=base_url, api_key=api_key, model=model, source='platform', **common)


def platform_model(env_name: str, jwt_secret: str, model_code: str = '') -> tuple[str, str, str]:
    """
    读取系统“AI 模型管理”中的模型（ai_models.status='0' 为启用），API Key 按平台 CryptoUtil 的方式解密
    （Fernet，密钥 = base64(sha256(JWT_SECRET_KEY))）

    :return: (base_url, api_key, model_code)
    """
    with psycopg2.connect(db_dsn(env_name)) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT model_code, base_url, api_key FROM ai_models
               WHERE status = '0' AND (%s = '' OR model_code = %s)
               ORDER BY model_sort NULLS LAST, model_id LIMIT 1""",
            (model_code, model_code),
        )
        row = cur.fetchone()
    if not row:
        return '', '', model_code
    code, base_url, encrypted = row
    if not encrypted:
        return base_url or '', '', code
    if not jwt_secret:
        raise RuntimeError('解密模型 Key 需要 .env 中的 JWT_SECRET_KEY')
    from cryptography.fernet import Fernet  # noqa: PLC0415

    fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(jwt_secret.encode()).digest()))
    return base_url or '', fernet.decrypt(encrypted.encode()).decode(), code


@dataclass
class DocInput:
    source_id: str
    version_id: int
    file_sha256: str
    parser_version: str
    meta: dict
    blocks: list[dict]


@dataclass
class RunResult:
    source_id: str
    status: str
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0
    rounds: int = 0
    output: dict | None = None
    issues: list[Issue] = field(default_factory=list)
    error: str | None = None
    cached: bool = False


def estimate_tokens(text: str) -> int:
    """
    粗估 token：中日韩字符约 0.7 token/字，其余约 4 字符/token
    """
    cjk = len(re.findall(r'[一-鿿぀-ヿ]', text))
    return int(cjk * 0.7 + (len(text) - cjk) / 4)


def load_doc(cur: psycopg2.extensions.cursor, source_id: str) -> DocInput | None:
    cur.execute(
        """SELECT v.version_id, v.file_sha256, v.parser_version, r.title, r.pub_year,
                  (SELECT design_label FROM lit_study_design s WHERE s.record_id = r.record_id AND s.is_current LIMIT 1)
           FROM lit_record r JOIN kb_document d ON d.record_id = r.record_id
           JOIN kb_document_version v ON v.document_id = d.document_id AND v.is_current
           WHERE r.source_id = %s ORDER BY v.version_id DESC LIMIT 1""",
        (source_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    version_id, sha, parser, title, year, design = row
    cur.execute(
        """SELECT block_key, section, block_type, page, text FROM kb_block
           WHERE version_id = %s AND section <> ALL(%s) ORDER BY seq""",
        (version_id, list(EXCLUDE_SECTIONS)),
    )
    blocks = [dict(zip(('block_key', 'section', 'block_type', 'page', 'text'), r, strict=True)) for r in cur.fetchall()]
    return DocInput(
        source_id, version_id, sha, parser or '', {'title': title, 'year': year, 'design_label': design}, blocks
    )


def split_blocks(blocks: list[dict], max_tokens: int) -> list[list[dict]]:
    """
    按阅读顺序把原文块切成若干段，每段估算 token 不超过 max_tokens（单个超长块独立成段，不拆块）
    """
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for b in blocks:
        t = estimate_tokens(b['text']) + 20
        if current and size + t > max_tokens:
            chunks.append(current)
            current, size = [], 0
        current.append(b)
        size += t
    if current:
        chunks.append(current)
    return chunks


def cache_key(doc: DocInput, model: str) -> str:
    raw = '|'.join(
        [
            doc.file_sha256,
            doc.parser_version,
            model,
            SCHEMA_VERSION,
            PROMPT_VERSION,
            hashlib.sha256(compact_schema().encode()).hexdigest(),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_json(text: str) -> dict:
    t = text.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', t, re.S)
    if m:
        t = m.group(1)
    start, end = t.find('{'), t.rfind('}')
    return json.loads(t[start : end + 1] if start >= 0 else t)


class Extractor:
    def __init__(self, settings: LlmSettings, mock_dir: Path | None = None) -> None:
        self.settings = settings
        self.mock_dir = mock_dir
        self.client = None
        if mock_dir is None:
            if not settings.api_key:
                raise RuntimeError(
                    '没有可用的模型 Key：请在“AI 模型管理”中启用模型，或在 .env.dev.local 配置 LIT_EXTRACT_API_KEY'
                )
            self.client = openai.OpenAI(
                api_key=settings.api_key, base_url=settings.base_url, timeout=settings.timeout, max_retries=0
            )
        self.system = system_prompt()

    def _chat(self, messages: list[dict], tag: str) -> tuple[str, int, int, bool]:
        """
        :param tag: 调用标识，如 2800、2800.r1、2800.skeleton、2800.c2（mock 模式下对应 <tag>.json）
        :return: (回复文本, 输入 token, 输出 token, 是否因输出上限被截断)
        """
        if self.mock_dir is not None:
            candidates = [tag, re.sub(r'\.r\d+$', '', tag), tag.split('.', maxsplit=1)[0]]
            path = next(p for p in (self.mock_dir / f'{c}.json' for c in candidates) if p.is_file())
            text = path.read_text(encoding='utf-8')
            truncated = text.startswith('<<TRUNCATED>>')
            return text, sum(estimate_tokens(m['content']) for m in messages), estimate_tokens(text), truncated
        kwargs: dict = {
            'model': self.settings.model,
            'messages': messages,
            'temperature': 0,
            'max_tokens': MAX_OUTPUT_TOKENS,
        }
        if self.settings.json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        if not self.settings.reasoning:
            # 火山方舟等平台的混合推理模型：显式关闭深度思考
            kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
        for attempt in range(5):
            try:
                r = self.client.chat.completions.create(**kwargs)
                usage = r.usage
                return (
                    r.choices[0].message.content or '',
                    getattr(usage, 'prompt_tokens', 0) or 0,
                    getattr(usage, 'completion_tokens', 0) or 0,
                    r.choices[0].finish_reason == 'length',
                )
            except openai.BadRequestError as e:  # noqa: PERF203  重试需要逐次捕获
                msg = str(e)
                if 'response_format' in msg and kwargs.pop('response_format', None):
                    continue
                if 'thinking' in msg and kwargs.pop('extra_body', None):  # 模型不支持该参数：本身无推理模式
                    continue
                if 'max_tokens' in msg and kwargs['max_tokens'] > 4096:  # noqa: PLR2004
                    kwargs['max_tokens'] //= 2
                    continue
                raise
            except TRANSIENT:
                time.sleep(min(60, 5 * 2**attempt))
        raise RuntimeError('模型多次超时或限流')

    def _converse(
        self, result: RunResult, user_content: str, tag: str, check: Callable[[dict], list[Issue]]
    ) -> tuple[dict | None, list[Issue], bool]:
        """
        一次抽取对话：调用模型 → 解析 → 校验 → 有错误时修复一轮。token 与轮次累加到 result

        :param check: 对模型输出做校验，返回问题清单（error 级会触发修复）
        :return: (输出, 问题清单, 是否被截断)
        """
        messages = [{'role': 'system', 'content': self.system}, {'role': 'user', 'content': user_content}]
        output: dict | None = None
        issues: list[Issue] = []
        for round_no in range(REPAIR_ROUNDS + 1):
            text, tin, tout, truncated = self._chat(messages, tag if round_no == 0 else f'{tag}.r{round_no}')
            result.tokens_in += tin
            result.tokens_out += tout
            result.rounds += 1
            if truncated:
                msg = f'输出达到上限 {MAX_OUTPUT_TOKENS} token 被截断'
                return None, [Issue('output_truncated', 'error', 'document', None, msg)], True
            try:
                output = parse_json(text)
            except (json.JSONDecodeError, ValueError) as e:
                output, issues = (
                    None,
                    [Issue('json_invalid', 'error', 'document', None, f'JSON 解析失败: {str(e)[:200]}')],
                )
            else:
                issues = check(output)
            blocking = [i for i in issues if i.severity == 'error']
            if not blocking or round_no == REPAIR_ROUNDS:
                break
            problems = '\n'.join(f'- [{i.rule_code}] {i.entity_ref or ""} {i.message}' for i in blocking[:40])
            messages += [
                {'role': 'assistant', 'content': text},
                {
                    'role': 'user',
                    'content': f'上面的 JSON 有以下问题，请修正后输出完整 JSON（仍须逐字引用原文、不得编造）：\n{problems}',
                },
            ]
        return output, issues, False

    def run(self, doc: DocInput, force_chunked: bool = False) -> RunResult:
        """
        先整篇抽取；输出被截断（或 force_chunked）时改用分段抽取
        """
        t0 = time.time()
        block_text = {b['block_key']: b['text'] for b in doc.blocks}
        result = RunResult(doc.source_id, 'failed')
        truncated = force_chunked
        if not force_chunked:
            prompt = user_prompt(str(doc.version_id), doc.meta, doc.blocks)
            output, issues, truncated = self._converse(result, prompt, doc.source_id, lambda o: validate(o, block_text))
            result.output, result.issues = output, issues
        if truncated:
            self._run_chunked(doc, block_text, result)
        if result.output is None and not result.error:
            result.error = next((i.message for i in result.issues if i.severity == 'error'), '抽取失败')
        result.status = (
            'to_review'
            if result.output is not None
            and not any(i.rule_code in ('schema_invalid', 'json_invalid') for i in result.issues)
            else 'failed'
        )
        result.seconds = round(time.time() - t0, 1)
        return result

    def _run_chunked(self, doc: DocInput, block_text: dict[str, str], result: RunResult) -> None:
        """
        分段抽取：① 整篇原文抽骨架（研究/人群/组别/结局等，findings 为空）；
        ② 原文按约 CHUNK_TOKENS 分段，每段在骨架基础上只抽该段的研究结果；③ 合并后整体校验
        """
        prompt = user_prompt(str(doc.version_id), doc.meta, doc.blocks) + SKELETON_NOTE
        skeleton, issues, truncated = self._converse(
            result, prompt, f'{doc.source_id}.skeleton', lambda o: validate(o, block_text)
        )
        if skeleton is None:
            result.output, result.issues = None, issues
            result.error = '分段抽取：骨架阶段失败' + ('（输出仍被截断）' if truncated else '')
            return
        skeleton['findings'] = []
        skeleton.pop('effect_modifications', None)
        base_messages = {i.message for i in validate(skeleton, block_text)}
        chunks = split_blocks(doc.blocks, CHUNK_TOKENS)
        merged = skeleton
        failed_chunks = []
        for idx, chunk in enumerate(chunks, 1):

            def check(part: dict, current: dict = merged) -> list[Issue]:
                # 只报告本段新引入的问题（骨架自身的问题已在骨架阶段处理）
                return [i for i in validate(merge_chunk(current, part), block_text) if i.message not in base_messages]

            part, _, part_truncated = self._converse(
                result, chunk_prompt(skeleton, chunk, idx, len(chunks)), f'{doc.source_id}.c{idx}', check
            )
            if part is None:
                failed_chunks.append(f'{idx}{"（截断）" if part_truncated else ""}')
                continue
            merged = merge_chunk(merged, part)
        result.output = merged
        result.issues = validate(merged, block_text)
        note = f'分段抽取：{len(chunks)} 段' + (f'，失败段 {failed_chunks}' if failed_chunks else '')
        result.issues.append(Issue('chunked_extraction', 'warn' if failed_chunks else 'info', 'document', None, note))


def save(conn: psycopg2.extensions.connection, doc: DocInput, key: str, settings: LlmSettings, r: RunResult) -> None:
    with conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO kb_extraction_run(version_id, cache_key, schema_version, model, prompt_hash, parser_version,
                                             status, tokens_in, tokens_out, raw_output, error, finish_time)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
               ON CONFLICT (cache_key) DO UPDATE SET status = EXCLUDED.status, tokens_in = EXCLUDED.tokens_in,
                 tokens_out = EXCLUDED.tokens_out, raw_output = EXCLUDED.raw_output, error = EXCLUDED.error,
                 finish_time = now()
               RETURNING run_id""",
            (
                doc.version_id,
                key,
                SCHEMA_VERSION,
                settings.model,
                hashlib.sha256(PROMPT_VERSION.encode()).hexdigest(),
                doc.parser_version,
                r.status,
                r.tokens_in,
                r.tokens_out,
                json.dumps(r.output, ensure_ascii=False) if r.output is not None else None,
                r.error,
            ),
        )
        run_id = cur.fetchone()[0]
        cur.execute('DELETE FROM kb_validator_issue WHERE run_id = %s', (run_id,))
        for i in r.issues:
            cur.execute(
                """INSERT INTO kb_validator_issue(run_id, entity_type, rule_code, severity, message)
                   VALUES (%s,%s,%s,%s,%s)""",
                (run_id, i.entity_type, i.rule_code, i.severity, f'{i.entity_ref or ""} {i.message}'.strip()),
            )


def revalidate(conn: psycopg2.extensions.connection, docs: list[DocInput], model: str) -> None:
    """
    对每篇最新一次成功抽取，按当前校验规则重算问题清单并替换（不调用模型）
    """
    total: Counter = Counter()
    for doc in docs:
        with conn, conn.cursor() as cur:
            cur.execute(
                """SELECT run_id, raw_output FROM kb_extraction_run
                   WHERE version_id = %s AND model = %s AND raw_output IS NOT NULL
                   ORDER BY finish_time DESC NULLS LAST LIMIT 1""",
                (doc.version_id, model),
            )
            row = cur.fetchone()
            if not row:
                continue
            run_id, output = row
            issues = validate(output, {b['block_key']: b['text'] for b in doc.blocks})
            cur.execute('DELETE FROM kb_validator_issue WHERE run_id = %s', (run_id,))
            for i in issues:
                cur.execute(
                    """INSERT INTO kb_validator_issue(run_id, entity_type, rule_code, severity, message)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (run_id, i.entity_type, i.rule_code, i.severity, f'{i.entity_ref or ""} {i.message}'.strip()),
                )
        kinds = Counter(i.rule_code for i in issues)
        total.update(kinds)
        if kinds:
            print(f'{doc.source_id:>6} {dict(kinds)}')
    print(f'重新校验 {len(docs)} 篇，问题合计 {dict(total)}')


def summarize(r: RunResult) -> str:
    o = r.output or {}
    counts = {
        k: len(o.get(k) or []) for k in ('studies', 'populations', 'findings', 'evidence_spans', 'recommendations')
    }
    kinds = Counter(i.rule_code for i in r.issues)
    kind = (o.get('document') or {}).get('doc_kind')
    return (
        f'{r.source_id:>6} {r.status:9} {"(缓存)" if r.cached else ""} 轮次 {r.rounds} 输入 {r.tokens_in:>6} '
        f'输出 {r.tokens_out:>5} {r.seconds:>5}s {kind or "-":24} {counts} 问题 {dict(kinds) or "无"}'
    )


def main() -> None:  # noqa: PLR0915  命令行流程
    ap = argparse.ArgumentParser(description='结构化抽取试跑')
    ap.add_argument('--env', default='dev')
    ap.add_argument('--ids', default='')
    ap.add_argument('--ids-file', default='')
    ap.add_argument('--dry-run', action='store_true', help='只估算输入 token')
    ap.add_argument('--mock-dir', default='')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--chunked', action='store_true', help='直接使用分段抽取（默认仅在输出被截断时自动启用）')
    ap.add_argument('--revalidate', action='store_true', help='用最新校验规则重新校验已有抽取结果（不调用模型）')
    ap.add_argument('--out-dir', default='', help='抽取 JSON 另存目录（默认 LIT_DATA_ROOT/_parsed/extract）')
    a = ap.parse_args()
    ids = [x.strip() for x in a.ids.split(',') if x.strip()]
    if a.ids_file:
        ids += Path(a.ids_file).read_text(encoding='utf-8').split()
    settings = LlmSettings.load(a.env)
    conn = psycopg2.connect(db_dsn(a.env))
    with conn.cursor() as cur:
        docs = [d for d in (load_doc(cur, i) for i in ids) if d]
    print(
        f'待抽取 {len(docs)} 篇（找不到原文层的 {len(ids) - len(docs)} 篇）；'
        f'模型 {settings.model}（来源：{"AI 模型管理" if settings.source == "platform" else ".env"}，深度思考关闭）'
    )

    if a.dry_run:
        sys_tokens = estimate_tokens(system_prompt())
        total = 0
        for d in docs:
            n = estimate_tokens(user_prompt(str(d.version_id), d.meta, d.blocks))
            total += n + sys_tokens
            print(
                f'{d.source_id:>6} 块 {len(d.blocks):>4}  估算输入 {n + sys_tokens:>7} token（其中系统提示 {sys_tokens}）'
            )
        print(f'合计估算输入 {total} token，平均每篇 {total // max(1, len(docs))}')
        return

    if a.revalidate:
        revalidate(conn, docs, settings.model)
        conn.close()
        return

    extractor = Extractor(settings, Path(a.mock_dir) if a.mock_dir else None)
    env = {**dotenv_values(BACKEND_ROOT / f'.env.{a.env}'), **os.environ}
    data_root = Path((env.get('LIT_DATA_ROOT') or '.').strip('\'"'))
    out_dir = Path(a.out_dir or data_root / '_parsed' / 'extract' / settings.model / PROMPT_VERSION)
    out_dir.mkdir(parents=True, exist_ok=True)

    def work(doc: DocInput) -> RunResult:
        key = cache_key(doc, settings.model if not a.mock_dir else f'mock:{settings.model}')
        if not a.force:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT status, tokens_in, tokens_out, raw_output FROM kb_extraction_run '
                    "WHERE cache_key = %s AND status = 'to_review'",
                    (key,),
                )
                hit = cur.fetchone()
            if hit:
                return RunResult(doc.source_id, hit[0], hit[1] or 0, hit[2] or 0, output=hit[3], cached=True)
        try:
            r = extractor.run(doc, force_chunked=a.chunked)
        except Exception as e:
            r = RunResult(doc.source_id, 'failed', error=f'{type(e).__name__}: {e}')
        save(conn, doc, key, settings, r)
        if r.output is not None:
            (out_dir / f'{doc.source_id}.json').write_text(
                json.dumps(r.output, ensure_ascii=False, indent=1), encoding='utf-8'
            )
        return r

    with ThreadPoolExecutor(max_workers=settings.concurrency) as ex:
        results = list(ex.map(work, docs))
    for r in results:
        print(summarize(r) + (f'  错误: {r.error}' if r.error else ''))
    tin, tout = sum(r.tokens_in for r in results), sum(r.tokens_out for r in results)
    cost = (tin * settings.price_in + tout * settings.price_out) / 1e6
    ok = sum(r.status == 'to_review' for r in results)
    print(
        f'成功 {ok}/{len(results)}；输入 {tin} token，输出 {tout} token'
        + (f'，估算费用 {cost:.2f} 元' if cost else '（未配置单价 LIT_EXTRACT_PRICE_IN/OUT）')
    )
    print(f'抽取 JSON 保存在 {out_dir}')
    conn.close()


if __name__ == '__main__':
    main()
