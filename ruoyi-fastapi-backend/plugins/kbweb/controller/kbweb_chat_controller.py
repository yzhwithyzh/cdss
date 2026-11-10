"""
循证问答接口（SSE 流式返回检索步骤与回答，免登录）
"""

import json
from typing import Annotated

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from common.aspect.db_session import DBSessionDependency
from common.router import APIRouterPro
from plugins.kbweb.agent.runner import run_events
from plugins.kbweb.service.kbweb_service import camel
from utils.log_util import logger

kbweb_chat_controller = APIRouterPro(prefix='/kbweb/public', order_num=41, tags=['循证知识库-问答'])


class ChatTurn(BaseModel):
    role: str
    content: str = Field(max_length=8000)


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


@kbweb_chat_controller.post('/chat', summary='循证问答（SSE）')
async def chat(request: Request, body: ChatBody, query_db: Annotated[AsyncSession, DBSessionDependency()]):
    # 工具调用可能并发，需要独立会话；用请求会话所在的引擎另建会话工厂
    factory = async_sessionmaker(bind=query_db.bind, expire_on_commit=False)

    async def stream():
        try:
            async for ev in run_events(factory, body.question.strip(), [t.model_dump() for t in body.history]):
                if await request.is_disconnected():
                    break
                if ev['type'] == 'tool_result':
                    ev = {**ev, 'content': ev['content'][:600]}
                elif ev['type'] == 'citations':
                    ev = camel(ev)
                yield f'data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n'
        except Exception as e:  # noqa: BLE001
            logger.exception('问答失败')
            yield f'data: {json.dumps({"type": "error", "message": str(e)[:300]}, ensure_ascii=False)}\n\n'
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
