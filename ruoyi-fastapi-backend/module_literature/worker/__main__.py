"""
文献导入与研究类型判定 worker（独立进程，可长时间运行）

用法（在 ruoyi-fastapi-backend 目录下）：
    .venv/Scripts/python.exe -m module_literature.worker --env=dev
    .venv/Scripts/python.exe -m module_literature.worker --env=dev --name=w2   # 同一主机再起一个

模型配置读取 .env.<env> 中的 LIT_LLM_* 变量；按 Ctrl+C 停止，未完成的文献在重启后继续。
"""

import argparse
import asyncio
import sys

from loguru import logger

from module_literature.worker.config import WorkerSettings
from module_literature.worker.runner import LiteratureWorker


def main() -> None:
    parser = argparse.ArgumentParser(description='文献导入与研究类型判定 worker')
    parser.add_argument('--env', default='dev', help='运行环境，对应 .env.<env> 配置文件')
    parser.add_argument('--name', default='w1', help='worker 名称，同一主机运行多个 worker 时需不同')
    args = parser.parse_args()

    settings = WorkerSettings.load(args.env, args.name)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level='INFO')
    logger.add(
        settings.log_dir / f'{args.name}_{{time:YYYY-MM-DD}}.log',
        level='INFO',
        rotation='00:00',
        retention='30 days',
        encoding='utf-8',
    )

    try:
        asyncio.run(LiteratureWorker(settings).run())
    except KeyboardInterrupt:
        logger.info('收到中断信号，worker 停止；未完成的文献将在下次启动时继续')


if __name__ == '__main__':
    main()
