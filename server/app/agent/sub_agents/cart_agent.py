"""Cart Agent — 购物车操作（3-8 阶段实现）"""
from typing import AsyncIterator
from app.models import events as ev


class CartAgent:
    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        yield ev.text_delta("Cart Agent 待实现（3-8 阶段）").to_sse()


cart_agent = CartAgent()
