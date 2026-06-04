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
from app.db.relational import (
    create_session,
    delete_session,
    get_all_messages,
    get_session,
    list_sessions,
)
from app.models import ChatRequest, ImageSearchRequest, SessionCreateResponse
from app.models import events as ev

router = APIRouter()


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_new_session():
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow()
    await create_session(sid)
    return SessionCreateResponse(session_id=sid, created_at=now)


@router.get("/sessions")
async def get_sessions():
    """会话列表 — 供客户端抽屉展示历史会话。"""
    return {"sessions": await list_sessions()}


@router.delete("/sessions/{session_id}")
async def delete_session_route(session_id: str):
    """删除会话及其全部关联数据（消息、购物车、订单）。"""
    await delete_session(session_id)
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """会话历史消息 — 供客户端回填对话（含商品卡富块）。"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = await get_all_messages(session_id)
    messages = [
        {
            "role": r["role"],
            "content": r["content"],
            "blocks": r.get("blocks", []),
            "timestamp": r.get("created_at"),
        }
        for r in rows
    ]
    return {"session_id": session_id, "messages": messages}


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
            import traceback; traceback.print_exc()
            yield ev.error("INTERNAL_ERROR", str(e)[:300]).to_sse()
            # 补发 done，防止客户端 agentState 卡在 checkout 等中间态
            yield ev.done(req.session_id, "browsing").to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/search/by-image")
async def search_by_image(req: ImageSearchRequest):
    session = await get_session(req.session_id)
    if not session:
        await create_session(req.session_id)

    async def event_stream():
        try:
            async for event_str in master_agent.run(
                session_id=req.session_id,
                message="",
                image_base64=req.image_base64,
            ):
                yield event_str
        except Exception as e:
            import traceback; traceback.print_exc()
            yield ev.error("INTERNAL_ERROR", str(e)[:300]).to_sse()
            yield ev.done(req.session_id, "browsing").to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
