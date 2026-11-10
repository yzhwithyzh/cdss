"""
问答用的大模型：Key 优先取 .env 的 LIT_LLM_API_KEY，否则取系统“AI 模型管理”中启用的模型（与抽取管线同一来源）。
模型名可用 LIT_CHAT_MODEL 覆盖（同一个方舟 Key 下的其他模型）。关闭深度思考，走 chat completions。
"""

import json
import re
from functools import lru_cache

from langchain_openai import ChatOpenAI

from tools.kb_pipeline.embed import load_env
from tools.kb_pipeline.extract import platform_model


@lru_cache(maxsize=4)
def chat_model(temperature: float = 0.1) -> ChatOpenAI:
    env = load_env('dev')
    base_url, api_key, model = env.get('LIT_LLM_BASE_URL'), env.get('LIT_LLM_API_KEY'), env.get('LIT_LLM_MODEL')
    if not api_key:
        base_url, api_key, model = platform_model('dev', env.get('JWT_SECRET_KEY', ''))
    return ChatOpenAI(
        model=env.get('LIT_CHAT_MODEL') or model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=float(env.get('LIT_LLM_TIMEOUT') or 120),
        max_retries=2,
        use_responses_api=False,
        extra_body={'thinking': {'type': 'disabled'}},
    )


REWRITE_PROMPT = """你是结直肠腺瘤/肠镜领域的检索专家。把下面的临床问题改写成检索英文文献题录用的检索词。
要求：
- 只围绕问题里**实际出现**的概念（病变类型、大小分档、病理、操作、结局、人群），不要添加问题没提到的主题
- 每个概念给英文标准术语，再加 1–2 个常见同义词或缩写；共 6–10 个检索短语，每个 1–4 个词
- 结局要写具体（如 metachronous advanced neoplasia、local recurrence、colorectal cancer incidence），不要写 risk、patient、study 这类泛词
- 不要包含年龄和具体数字
只输出 JSON：{"keywords": [...]}

问题：{q}"""


async def rewrite_query(q: str) -> list[str]:
    msg = await chat_model(0.0).ainvoke(REWRITE_PROMPT.replace('{q}', q))
    m = re.search(r'\{.*\}', str(msg.content), re.S)
    try:
        return [k.strip() for k in json.loads(m.group(0))['keywords'] if k and k.strip()] if m else []
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
