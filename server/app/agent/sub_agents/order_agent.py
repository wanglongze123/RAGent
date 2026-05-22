"""Order Agent — 下单引导（3-9 阶段实现）"""
from typing import AsyncIterator
from app.models import events as ev


class OrderAgent:
    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        yield ev.text_delta("Order Agent 待实现（3-9 阶段）").to_sse()


order_agent = OrderAgent()
