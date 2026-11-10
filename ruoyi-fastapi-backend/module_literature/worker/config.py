"""
文献判定 worker 配置：与 Web 服务共用 .env.<env> 文件
"""

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _strip(value: str | None) -> str:
    return (value or '').strip().strip('\'"')


def upload_dir_from_env(env: dict[str, str | None]) -> Path:
    """
    上传 XML 的保存目录：LIT_UPLOAD_DIR，未配置时为 LIT_DATA_ROOT/uploads

    :param env: 环境变量字典
    :return: 目录路径
    """
    upload_dir = _strip(env.get('LIT_UPLOAD_DIR'))
    if upload_dir:
        return Path(upload_dir)
    data_root = _strip(env.get('LIT_DATA_ROOT'))
    return Path(data_root) / 'uploads' if data_root else BACKEND_ROOT / 'uploads' / 'literature'


@dataclass(frozen=True)
class WorkerSettings:
    """
    worker 运行配置
    """

    env_name: str
    worker_id: str
    db_dsn: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_timeout: float
    llm_json_mode: bool
    concurrency: int
    max_attempts: int
    poll_interval: float
    heartbeat_interval: float
    stale_seconds: int
    log_dir: Path

    @classmethod
    def load(cls, env_name: str, worker_name: str) -> 'WorkerSettings':
        """
        从 .env.<env> 读取配置（进程环境变量优先）

        :param env_name: 运行环境，如 dev、prod
        :param worker_name: worker 名称，同一主机运行多个 worker 时用于区分
        :return: 配置对象
        """
        env_file = BACKEND_ROOT / f'.env.{env_name}'
        if not env_file.is_file():
            raise FileNotFoundError(f'未找到配置文件 {env_file}')
        env = {**dotenv_values(env_file), **os.environ}
        db = json.loads(env['DB_SOURCES'])[_strip(env.get('DB_DEFAULT_SOURCE')) or 'primary']
        if db['db_type'] != 'postgresql':
            raise ValueError('文献判定 worker 仅支持 PostgreSQL')
        db_dsn = (
            f'postgresql://{db["db_username"]}:{db["db_password"]}@{db["db_host"]}:{db["db_port"]}/{db["db_database"]}'
        )
        return cls(
            env_name=env_name,
            worker_id=f'{socket.gethostname()}:{worker_name}',
            db_dsn=db_dsn,
            llm_base_url=_strip(env.get('LIT_LLM_BASE_URL')) or 'https://ark.cn-beijing.volces.com/api/v3',
            llm_api_key=_strip(env.get('LIT_LLM_API_KEY')),
            llm_model=_strip(env.get('LIT_LLM_MODEL')) or 'deepseek-v3-2-251201',
            llm_temperature=float(_strip(env.get('LIT_LLM_TEMPERATURE')) or 0),
            llm_timeout=float(_strip(env.get('LIT_LLM_TIMEOUT')) or 120),
            llm_json_mode=(_strip(env.get('LIT_LLM_JSON_MODE')) or 'true').lower() == 'true',
            concurrency=max(1, int(_strip(env.get('LIT_LLM_CONCURRENCY')) or 8)),
            max_attempts=max(1, int(_strip(env.get('LIT_LLM_MAX_ATTEMPTS')) or 3)),
            poll_interval=float(_strip(env.get('LIT_WORKER_POLL_SECONDS')) or 3),
            heartbeat_interval=float(_strip(env.get('LIT_WORKER_HEARTBEAT_SECONDS')) or 15),
            stale_seconds=int(_strip(env.get('LIT_WORKER_STALE_SECONDS')) or 120),
            log_dir=BACKEND_ROOT / 'logs' / 'literature_worker',
        )
