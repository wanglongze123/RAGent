"""
对话接口（Phase 1 最简版） — 单轮 RAG，纯向量检索。

流程：
  1. 推 thinking 事件
  2. 推 tool_progress 事件
  3. 调向量检索拿 Top-K 商品
  4. 推 product_card 事件（每张卡片字段从 product_repo 取，不经模型）
  5. 拼 Prompt 调豆包流式生成推荐理由
  6. 逐 token 推 text_delta 事件
  7. 推 done 事件

注意：Phase 3 这部分会被 Master Agent 接管，加状态机和子 Agent 路由。
现在先用最朴素的链路把闭环跑起来。
"""
import uuid
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, SessionCreateResponse
from app.models import events as ev
from app.rag.retriever import retriever
from app.db.product_repo import product_repo
from app.llm.client import llm_client


router = APIRouter()


# 会话内存存储（Phase 3 换成 SQLite）
_sessions: dict[str, dict] = {}


SYSTEM_PROMPT = """你是一个专业的电商导购助手。请基于下方提供的【商品资料】回答用户问题，遵守以下规则：

1. 只能使用资料中明确提到的信息，不要编造商品功效、价格、品牌、优惠等任何信息。
2. 回答时不要复述具体价格数字（价格由系统在商品卡片中展示），如需提及可说"请查看商品卡片中的价格"。
3. 推荐时给出简短理由，体现专业性，避免冗长。
4. 用自然、友好的口吻，像真实导购一样与用户对话。
5. 如果资料不足以回答，主动反问用户偏好（预算、肤质、使用场景等）以收敛需求。
"""


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session():
    sid = f"sess_{uuid.uuid4().hex[:12]}"
    now = datetime.utcnow()
    _sessions[sid] = {"created_at": now, "messages": []}
    return SessionCreateResponse(session_id=sid, created_at=now)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式对话入口"""
    return StreamingResponse(
        _generate_chat_stream(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁止 nginx 缓冲，保证低延迟
        },
    )


async def _generate_chat_stream(req: ChatRequest) -> AsyncIterator[str]:
    """SSE 事件生成器 — 整个对话的核心编排逻辑"""
    try:
        # 1) 思考中
        yield ev.thinking("正在理解您的需求...").to_sse()

        # 2) 检索进行中
        yield ev.tool_progress("vector_search", "正在为您检索相关商品...").to_sse()

        # 3) 向量检索（商品级聚合）
        ranked_products = await retriever.retrieve_products(
            query=req.message,
            top_k_chunks=12,
            top_k_products=5,
        )

        # 4) 推商品卡片 — 字段全部从 product_repo 取，模型不经手
        for rp in ranked_products:
            product = product_repo.get(rp["product_id"])
            if not product:
                continue
            yield ev.product_card(
                product_id=product.product_id,
                title=product.title,
                brand=product.brand,
                image_url=product.image_url,
                price=product.base_price,
                sub_category=product.sub_category,
            ).to_sse()

        # 5) 拼检索上下文，调 LLM 流式生成推荐理由
        context = _build_context(ranked_products)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"【商品资料】\n{context}\n\n【用户问题】\n{req.message}"},
        ]

        async for delta in llm_client.chat_stream(messages, temperature=0.7):
            yield ev.text_delta(delta).to_sse()

        # 6) 完成
        yield ev.done(req.session_id, agent_state="browsing").to_sse()

    except Exception as e:
        yield ev.error("INTERNAL_ERROR", f"服务异常: {str(e)}").to_sse()


def _build_context(ranked_products: list[dict]) -> str:
    """把检索结果拼成 prompt 用的资料块"""
    parts = []
    for i, rp in enumerate(ranked_products, 1):
        product = product_repo.get(rp["product_id"])
        if not product:
            continue
        # 把命中的 chunk 内容拼起来作为该商品的资料
        chunk_texts = "\n".join(c.content for c in rp["hit_chunks"][:3])
        parts.append(f"【商品{i}】{product.title}\n品牌:{product.brand}\n类目:{product.sub_category}\n{chunk_texts}")
    return "\n\n---\n\n".join(parts)
