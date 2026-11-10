"""
OpenAI 兼容接口的调用封装（火山方舟、DeepSeek、通义等均可）
"""

import asyncio
import random
from dataclasses import dataclass

import openai
from loguru import logger
from openai import AsyncOpenAI

from module_literature.worker.config import WorkerSettings

# 鉴权、余额、模型不存在等错误重试无意义，应暂停任务等待人工处理
FATAL_ERRORS = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)
TRANSIENT_ERRORS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)
TRANSIENT_RETRIES = 4


class FatalLlmError(Exception):
    """
    需要暂停任务的模型调用错误
    """


@dataclass
class LlmReply:
    """
    模型回复
    """

    content: str
    prompt_tokens: int
    completion_tokens: int


class LlmClient:
    """
    模型调用客户端：瞬时错误指数退避重试，致命错误抛出 FatalLlmError
    """

    def __init__(self, settings: WorkerSettings) -> None:
        if not settings.llm_api_key:
            raise FatalLlmError('未配置 LIT_LLM_API_KEY')
        self.settings = settings
        self.json_mode = settings.llm_json_mode
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            max_retries=0,
        )

    async def complete(self, system_prompt: str, user_prompt: str) -> LlmReply:
        """
        发送一次对话请求

        :param system_prompt: 系统提示词
        :param user_prompt: 用户提示词
        :return: 模型回复
        """
        for retry in range(TRANSIENT_RETRIES + 1):
            try:
                return await self._request(system_prompt, user_prompt)
            except FATAL_ERRORS as e:  # noqa: PERF203 重试循环需要逐次捕获
                raise FatalLlmError(f'{type(e).__name__}: {e}') from e
            except openai.BadRequestError as e:
                # 部分模型不支持 response_format，关闭 JSON 模式后重试一次
                if self.json_mode and 'response_format' in str(e):
                    logger.warning('模型不支持 response_format，改为普通模式')
                    self.json_mode = False
                    continue
                raise
            except TRANSIENT_ERRORS as e:
                if retry == TRANSIENT_RETRIES:
                    raise
                delay = min(60.0, 2 ** (retry + 1)) + random.uniform(0, 1)
                logger.warning(f'模型调用暂时失败（{type(e).__name__}），{delay:.1f}秒后重试')
                await asyncio.sleep(delay)
        raise RuntimeError('unreachable')

    async def _request(self, system_prompt: str, user_prompt: str) -> LlmReply:
        kwargs = {'response_format': {'type': 'json_object'}} if self.json_mode else {}
        response = await self.client.chat.completions.create(
            model=self.settings.llm_model,
            temperature=self.settings.llm_temperature,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            **kwargs,
        )
        usage = response.usage
        return LlmReply(
            content=response.choices[0].message.content or '',
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def close(self) -> None:
        await self.client.close()
