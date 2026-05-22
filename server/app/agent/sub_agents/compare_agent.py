"""Compare Agent — 多商品对比（3-7 阶段实现）"""
from typing import AsyncIterator
from app.models import events as ev


class CompareAgent:
    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        yield ev.text_delta("Compare Agent 待实现（3-7 阶段）").to_sse()


compare_agent = CompareAgent()
