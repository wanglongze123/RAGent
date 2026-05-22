"""
对话接口 — Phase 3 版本，接入 Master Agent。

所有业务逻辑下沉到 Master Agent + 子 Agent，
这里只负责：接收请求 → 创建/验证会话 → 转发 SSE 流。
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent.master_agent import master_agent
from app.db.relational import create_session, get_session
from app.models import ChatRequest, SessionCreateResponse
from app.models import events as ev

router = APIRouter()


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_new_session():
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow()
    await create_session(sid)
    return SessionCreateResponse(session_id=sid, created_at=now)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    # 会话不存在时自动创建（兼容客户端直接带 session_id 的情况）
    session = await get_session(req.session_id)
    if not session:
        await create_session(req.session_id)

    async def event_stream():
        try:
            async for event_str in master_agent.run(
                session_id=req.session_id,
                message=req.message,
                image_base64=req.image_base64,
            ):
                yield event_str
        except Exception as e:
            yield ev.error("INTERNAL_ERROR", str(e)).to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
