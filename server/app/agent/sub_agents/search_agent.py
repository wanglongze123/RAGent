"""Search Agent — 商品搜索与推荐（3-6 阶段实现）"""
from typing import AsyncIterator
from app.models import events as ev


class SearchAgent:
    async def run(
        self,
        session_id: str,
        message: str,
        params: dict,
        session: dict,
        image_base64: str | None = None,
    ) -> AsyncIterator[str]:
        yield ev.text_delta("Search Agent 待实现（3-6 阶段）").to_sse()


search_agent = SearchAgent()
